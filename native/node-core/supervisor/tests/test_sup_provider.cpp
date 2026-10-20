// The provider: a real process boundary against a FAKE systemctl.
//
// The fake is a shell script in a temporary directory. The provider is handed
// its path as `systemctl_path`, so no test can reach this host's service
// manager by accident and no unit on this machine is read, started, stopped or
// restarted by anything in this file.
//
// Two of the checks are about absence rather than about output: a refused unit
// must leave the fake's marker file UNCREATED, because "the refusal happened
// before the process started" is a claim about what did not run, and the only
// honest way to test it is to look for the evidence that would exist if it had.
#include <sys/stat.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/manifest.hpp"
#include "supervisor/systemd_user.hpp"

using namespace nodecore::supervisor;

namespace {

std::string shell_quote(const std::string& value) { return "'" + value + "'"; }

// A fake `systemctl`. Its behaviour is selected by FAKE_SYSTEMCTL_MODE, which
// the child inherits from this process.
std::string write_fake_systemctl(const std::string& directory) {
    const std::string path = directory + "/fake-systemctl";
    const std::string marker = directory + "/marker";
    std::ofstream script(path);
    script << "#!/bin/sh\n"
           << "echo \"$@\" >> " << shell_quote(marker) << "\n"
           << "case \"$FAKE_SYSTEMCTL_MODE\" in\n"
           << "  exit3) echo 'no such unit' >&2; exit 3 ;;\n"
           << "  sleep) sleep 30; exit 0 ;;\n"
           << "esac\n"
           << "for a in \"$@\"; do\n"
           << "  if [ \"$a\" = \"show\" ]; then\n"
           << "    case \"$FAKE_SYSTEMCTL_MODE\" in\n"
           << "      unknown-state) echo 'ActiveState=flurbling'; echo 'SubState=none'; exit 0 ;;\n"
           << "      no-state) exit 0 ;;\n"
           << "      *) echo 'ActiveState=active'; echo 'SubState=running'; exit 0 ;;\n"
           << "    esac\n"
           << "  fi\n"
           << "done\n"
           << "exit 0\n";
    script.close();
    ::chmod(path.c_str(), 0755);
    return path;
}

// One place that runs a shell, so the warning about ignoring `system`'s return
// value is silenced once. Nothing else in this file reaches a shell, and the
// code under test never does.
void run_shell(const std::string& command) {
    const int status = ::system(command.c_str());
    (void)status;
}

bool marker_exists(const std::string& directory) {
    std::ifstream marker(directory + "/marker");
    return marker.good();
}

std::string marker_text(const std::string& directory) {
    std::ifstream marker(directory + "/marker");
    std::ostringstream buffer;
    buffer << marker.rdbuf();
    return buffer.str();
}

void set_mode(const char* mode) { ::setenv("FAKE_SYSTEMCTL_MODE", mode, 1); }

bool contains(const std::vector<std::string>& values, const std::string& wanted) {
    for (const std::string& value : values) {
        if (value == wanted) {
            return true;
        }
    }
    return false;
}

}  // namespace

int main() {
    // A temporary directory under the test's own control.
    const std::string directory = "/tmp/serveros-supervisor-provider-test";
    run_shell("rm -rf " + directory + " && mkdir -p " + directory);
    const std::string fake = write_fake_systemctl(directory);

    CHECK(valid_unit_id("serveros-fleet-node.service"));

    // ------------------------------------------------------------------- argv

    {
        const std::vector<std::string> argv =
            systemctl_verb_argv(fake, "restart", "serveros-fleet-node.service");
        CHECK_EQ(argv.size(), std::size_t{7});
        CHECK_EQ(argv[0], fake);
        CHECK(contains(argv, std::string("--user")));
        CHECK(contains(argv, std::string("restart")));
        // The unit is its own element, at the end, after `--`.
        CHECK_EQ(argv.back(), std::string("serveros-fleet-node.service"));
        CHECK_EQ(argv[argv.size() - 2], std::string("--"));
        // There is no shell here, and nothing that would make one.
        CHECK(!contains(argv, std::string("sh")));
        CHECK(!contains(argv, std::string("-c")));
        CHECK(!contains(argv, std::string("bash")));
    }

    // ------------------------------------------------- a unit outside the rules

    const UnitAllowlistBuilt built =
        UnitAllowlist::build({"serveros-fleet-node.service", "serveros-tunnel.service"});
    CHECK(built.ok);
    {
        // A unit id that is not one is not allowlist-able at all, and the
        // refusal names it rather than dropping it.
        const UnitAllowlistBuilt bad = UnitAllowlist::build({"a.service; rm -rf /"});
        CHECK(!bad.ok);
        CHECK_CONTAINS(bad.error, "not a systemd user unit id");
    }
    {
        const UnitAllowlistBuilt dup =
            UnitAllowlist::build({"a.service", "a.service"});
        CHECK(!dup.ok);
        CHECK_CONTAINS(dup.error, "twice");
    }

    auto clock = [] { return std::string("2026-10-14T00:00:00+00:00"); };
    SystemdUserProvider provider(fake, built.allowlist, clock, /*timeout_seconds=*/2);

    set_mode("ok");
    {
        run_shell("rm -f " + directory + "/marker");
        const ProviderResult out = provider.start("evil; rm -rf /.service");
        CHECK(!out.ok);
        CHECK_EQ(out.code, std::string("UNIT_ID_INVALID"));
        // The refusal happened BEFORE anything ran, which is a claim about a
        // process that does not exist — so it is checked by looking for the
        // evidence that would exist if it had.
        CHECK(!marker_exists(directory));
    }
    {
        run_shell("rm -f " + directory + "/marker");
        const ProviderResult out = provider.restart("someone-elses.service");
        CHECK(!out.ok);
        CHECK_EQ(out.code, std::string("UNIT_NOT_ALLOWED"));
        CHECK(!marker_exists(directory));

        const Observed observed = provider.query("someone-elses.service");
        CHECK(!observed.measured);
        CHECK_CONTAINS(observed.reason, "not in this Core's allowlist");
        CHECK(observed.active == Active::Unknown);
        CHECK(!marker_exists(directory));
    }

    // ----------------------------------------------------- the allowlisted unit

    {
        run_shell("rm -f " + directory + "/marker");
        const ProviderResult out = provider.start("serveros-fleet-node.service");
        CHECK(out.ok);
        CHECK_EQ(out.code, std::string("EXITED"));
        CHECK_EQ(out.exit_code, 0);
        // The fake recorded exactly the argv it was given, with the unit as one
        // element — which is what "no shell" means at the process boundary.
        const std::string recorded = marker_text(directory);
        CHECK_CONTAINS(recorded, "--user");
        CHECK_CONTAINS(recorded, "start");
        CHECK_CONTAINS(recorded, "serveros-fleet-node.service");
    }
    {
        set_mode("exit3");
        run_shell("rm -f " + directory + "/marker");
        const ProviderResult out = provider.start("serveros-fleet-node.service");
        CHECK(!out.ok);
        CHECK_EQ(out.code, std::string("EXITED"));
        CHECK_EQ(out.exit_code, 3);
        CHECK_CONTAINS(out.reason, "exited 3");
    }

    // ------------------------------------------------------- the bound fires

    {
        set_mode("sleep");
        const ProviderResult out = provider.start("serveros-fleet-node.service");
        CHECK(!out.ok);
        CHECK_EQ(out.code, std::string("TIMED_OUT"));
        CHECK_CONTAINS(out.reason, "process group was killed");
    }

    // --------------------------------------------------------------- reading

    set_mode("ok");
    {
        const Observed observed = provider.query("serveros-fleet-node.service");
        CHECK(observed.measured);
        CHECK(observed.active == Active::Active);
        CHECK_EQ(observed.sub_state, std::string("running"));
        CHECK_EQ(observed.source, std::string("systemd-user"));
        CHECK_EQ(observed.age_seconds, 0L);
        CHECK_EQ(observed.observed_at, std::string("2026-10-14T00:00:00+00:00"));
    }
    {
        set_mode("unknown-state");
        const Observed observed = provider.query("serveros-fleet-node.service");
        // A state word a newer systemd invented is NOT a fault here.
        CHECK(!observed.measured);
        CHECK(observed.active == Active::Unknown);
        CHECK_CONTAINS(observed.reason, "does not know");
    }
    {
        set_mode("no-state");
        const Observed observed = provider.query("serveros-fleet-node.service");
        CHECK(!observed.measured);
        CHECK_CONTAINS(observed.reason, "not an inactive one");
    }
    {
        // The parser, without a process.
        const Observed row = parse_systemctl_show("a.service", "ActiveState=failed\nSubState=failed\n",
                                                  "2026-10-14T00:00:00+00:00");
        CHECK(row.measured);
        CHECK(row.active == Active::Failed);
        const Observed blank = parse_systemctl_show("a.service", "garbage without an equals sign\n", "");
        CHECK(!blank.measured);
    }

    run_shell("rm -rf " + directory);
    return check::report("test_sup_provider");
}
