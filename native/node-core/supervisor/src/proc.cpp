#include "proc.hpp"

#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <string>

namespace nodecore::supervisor::detail {
namespace {

long monotonic_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

void close_if_open(int fd) {
    if (fd >= 0) {
        ::close(fd);
    }
}

}  // namespace

RunResult run_argv(const std::vector<std::string>& argv,
                   const std::map<std::string, std::string>& env_overrides, int timeout_seconds,
                   std::size_t max_output_bytes) {
    RunResult result;
    if (argv.empty()) {
        result.code = "START_FAILED";
        result.reason = "argv is empty";
        return result;
    }
    if (timeout_seconds <= 0) {
        result.code = "START_FAILED";
        result.reason = "timeout_seconds must be positive";
        return result;
    }

    int out_pipe[2] = {-1, -1};
    int err_pipe[2] = {-1, -1};
    if (::pipe(out_pipe) != 0) {
        result.code = "START_FAILED";
        result.reason = std::string("pipe() failed: ") + std::strerror(errno);
        return result;
    }
    if (::pipe(err_pipe) != 0) {
        close_if_open(out_pipe[0]);
        close_if_open(out_pipe[1]);
        result.code = "START_FAILED";
        result.reason = std::string("pipe() failed: ") + std::strerror(errno);
        return result;
    }

    const pid_t pid = ::fork();
    if (pid < 0) {
        close_if_open(out_pipe[0]);
        close_if_open(out_pipe[1]);
        close_if_open(err_pipe[0]);
        close_if_open(err_pipe[1]);
        result.code = "START_FAILED";
        result.reason = std::string("fork() failed: ") + std::strerror(errno);
        return result;
    }
    if (pid == 0) {
        // Its own session, so `kill(-pid)` reaches anything the command forked.
        ::setsid();
        ::dup2(out_pipe[1], STDOUT_FILENO);
        ::dup2(err_pipe[1], STDERR_FILENO);
        close_if_open(out_pipe[0]);
        close_if_open(out_pipe[1]);
        close_if_open(err_pipe[0]);
        close_if_open(err_pipe[1]);
        for (const auto& entry : env_overrides) {
            ::setenv(entry.first.c_str(), entry.second.c_str(), 1);
        }
        std::vector<char*> args;
        args.reserve(argv.size() + 1);
        for (const std::string& value : argv) {
            args.push_back(const_cast<char*>(value.c_str()));
        }
        args.push_back(nullptr);
        ::execvp(args[0], args.data());
        ::_exit(127);  // exec failed; the parent reads 127 and says so
    }

    close_if_open(out_pipe[1]);
    close_if_open(err_pipe[1]);

    const long deadline = monotonic_ms() + static_cast<long>(timeout_seconds) * 1000;
    bool out_open = true;
    bool err_open = true;
    bool truncated = false;
    char buffer[4096];
    while (out_open || err_open) {
        const long remaining = deadline - monotonic_ms();
        if (remaining <= 0) {
            break;
        }
        struct pollfd entries[2];
        int count = 0;
        if (out_open) {
            entries[count++] = pollfd{out_pipe[0], POLLIN, 0};
        }
        if (err_open) {
            entries[count++] = pollfd{err_pipe[0], POLLIN, 0};
        }
        // Woken at least once a second so the deadline is re-checked even when
        // the child is silent. A single poll for the whole remaining time would
        // be a bound that only fires when the child chooses to write.
        const int slice = static_cast<int>(std::min<long>(remaining, 1000));
        const int ready = ::poll(entries, static_cast<nfds_t>(count), slice);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        if (ready == 0) {
            continue;
        }
        for (int i = 0; i < count; ++i) {
            if (entries[i].revents == 0) {
                continue;
            }
            const bool is_out = entries[i].fd == out_pipe[0];
            const ssize_t got = ::read(entries[i].fd, buffer, sizeof(buffer));
            if (got > 0) {
                std::string& target = is_out ? result.stdout_text : result.stderr_text;
                if (target.size() < max_output_bytes) {
                    target.append(buffer, std::min<std::size_t>(static_cast<std::size_t>(got),
                                                                max_output_bytes - target.size()));
                } else {
                    truncated = true;
                }
            } else if (got == 0) {
                if (is_out) {
                    out_open = false;
                } else {
                    err_open = false;
                }
            } else if (errno != EINTR) {
                if (is_out) {
                    out_open = false;
                } else {
                    err_open = false;
                }
            }
        }
    }

    int status = 0;
    bool timed_out = false;
    while (true) {
        const pid_t done = ::waitpid(pid, &status, WNOHANG);
        if (done == pid) {
            break;
        }
        if (done < 0 && errno != EINTR) {
            break;
        }
        if (monotonic_ms() >= deadline) {
            timed_out = true;
            ::kill(-pid, SIGKILL);
            ::kill(pid, SIGKILL);
            ::waitpid(pid, &status, 0);
            break;
        }
        ::usleep(5000);
    }

    close_if_open(out_pipe[0]);
    close_if_open(err_pipe[0]);

    if (timed_out) {
        result.code = "TIMED_OUT";
        result.reason = "the command did not finish within " + std::to_string(timeout_seconds) +
                        "s and its process group was killed";
        return result;
    }
    if (WIFEXITED(status)) {
        result.code = "EXITED";
        result.exit_code = WEXITSTATUS(status);
    } else {
        result.code = "SIGNALLED";
        result.reason = "the command was killed by a signal";
    }
    if (truncated) {
        result.reason += (result.reason.empty() ? "" : "; ");
        result.reason += "output was truncated at " + std::to_string(max_output_bytes) + " bytes";
    }
    result.ok = result.code == "EXITED" && result.exit_code == 0;
    return result;
}

}  // namespace nodecore::supervisor::detail
