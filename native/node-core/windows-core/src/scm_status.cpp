// The Windows service-status parser. See `scm_status.hpp` for why it is not a
// port of the Linux module.
#include "windows-core/scm_status.hpp"

#include <cctype>
#include <string>

#include "supervisor/admission.hpp"

namespace nodecore::windows_core {

namespace {

std::string trim(const std::string& raw) {
    std::size_t begin = 0;
    std::size_t end = raw.size();
    while (begin < end && std::isspace(static_cast<unsigned char>(raw[begin])) != 0) {
        ++begin;
    }
    while (end > begin && std::isspace(static_cast<unsigned char>(raw[end - 1])) != 0) {
        --end;
    }
    return raw.substr(begin, end - begin);
}

//: `sc` prints its failures on stderr with a bracketed numeric code:
//: `[SC] OpenService FAILED 1060:`. The code is the fact; the sentence after it
//: is localised, so only the number is matched.
bool mentions_error(const std::string& text, int code) {
    const std::string needle = std::to_string(code);
    std::size_t at = text.find(needle);
    while (at != std::string::npos) {
        // A bare substring match would let 11060 or a path containing 1060
        // answer as this error, so the digits must not be part of a longer run.
        const bool left_ok = at == 0 || std::isdigit(static_cast<unsigned char>(text[at - 1])) == 0;
        const std::size_t after = at + needle.size();
        const bool right_ok =
            after >= text.size() || std::isdigit(static_cast<unsigned char>(text[after])) == 0;
        if (left_ok && right_ok) {
            return true;
        }
        at = text.find(needle, at + 1);
    }
    return false;
}

//: The first integer in a token sequence, e.g. `4  RUNNING` or `3   DEMAND_START`.
bool leading_int(const std::string& text, int& out) {
    std::size_t index = 0;
    while (index < text.size() && std::isspace(static_cast<unsigned char>(text[index])) != 0) {
        ++index;
    }
    const std::size_t start = index;
    while (index < text.size() && std::isdigit(static_cast<unsigned char>(text[index])) != 0) {
        ++index;
    }
    if (index == start) {
        return false;
    }
    out = std::stoi(text.substr(start, index - start));
    return true;
}

//: `dwCurrentState`, by number. The NUMBER is read rather than the word beside
//: it for the same reason the Linux parser reads `LoadState` numerically-adjacent
//: fields: the word is localised on some Windows builds and the number is not.
std::string state_word_for(int raw) {
    switch (raw) {
        case 1:
            return kStateStopped;
        case 2:
            return kStateStartPending;
        case 3:
            return kStateStopPending;
        case 4:
            return kStateRunning;
        case 5:
            return kStateContinuePending;
        case 6:
            return kStatePausePending;
        case 7:
            return kStatePaused;
        default:
            // Not a state this version knows. NOT a fault: a newer Windows may
            // add one, and reporting it as stopped would be a claim about the
            // host that the answer does not support.
            return std::string();
    }
}

//: `dwStartType`, by number. `2` is AUTO_START, `3` DEMAND_START, `4` DISABLED;
//: `0` and `1` are the boot and system start types a driver may use.
std::string start_type_for(int raw) {
    switch (raw) {
        case 2:
            return kStartAuto;
        case 3:
            return kStartDemand;
        case 4:
            return kStartDisabled;
        default:
            return std::string();
    }
}

WindowsServiceObservation unread(const std::string& name, const std::string& reason) {
    WindowsServiceObservation observed;
    observed.service_name = name;
    observed.state = kStateUnknown;
    observed.measured = false;
    observed.reason = reason;
    return observed;
}

}  // namespace

WindowsServiceObservation parse_scm_query(const std::string& service_name,
                                          const std::string& stdout_text,
                                          const std::string& stderr_text, int exit_code) {
    // THE TWO NAMED ERRORS FIRST, and they are MEASUREMENTS rather than
    // failures. `1060` says the SCM was asked and there is no such service;
    // `1069` says there is one and it cannot log on. Both are answers about the
    // host, and both are states a reader must not have to infer from silence.
    if (exit_code == 1060 || mentions_error(stderr_text, 1060)) {
        WindowsServiceObservation observed = unread(service_name, kReasonNotInstalled);
        observed.state = kStateNotInstalled;
        observed.measured = true;  // the question WAS answered
        return observed;
    }
    if (exit_code == 1069 || mentions_error(stderr_text, 1069)) {
        WindowsServiceObservation observed = unread(service_name, kReasonNeedsLogin);
        observed.state = kStateNeedsLogin;
        observed.measured = true;
        return observed;
    }

    std::string state_text;
    std::string start_text;
    std::size_t begin = 0;
    while (begin <= stdout_text.size()) {
        const std::size_t newline = stdout_text.find('\n', begin);
        const std::size_t end = newline == std::string::npos ? stdout_text.size() : newline;
        const std::string line = trim(stdout_text.substr(begin, end - begin));
        begin = newline == std::string::npos ? stdout_text.size() + 1 : newline + 1;

        const std::size_t colon = line.find(':');
        if (colon == std::string::npos) {
            // `sc` indents the state's qualifiers on their own line
            // `(STOPPABLE, NOT_PAUSABLE, ...)`. They are not fields, and reading
            // them as fields is how a state word gets invented.
            continue;
        }
        const std::string key = trim(line.substr(0, colon));
        const std::string value = trim(line.substr(colon + 1));
        if (key == "STATE") {
            state_text = value;
        } else if (key == "START_TYPE") {
            start_text = value;
        }
    }

    if (state_text.empty()) {
        // AN ABSENT STATE IS NOT A STOPPED SERVICE. This is the same rule the
        // Linux side applies when `systemctl` prints no `ActiveState`.
        return unread(service_name, kReasonNoStateLine);
    }

    int raw_state = 0;
    if (!leading_int(state_text, raw_state)) {
        return unread(service_name, kReasonUnknownStateWord);
    }
    const std::string word = state_word_for(raw_state);
    if (word.empty()) {
        return unread(service_name, kReasonUnknownStateWord);
    }

    WindowsServiceObservation observed;
    observed.service_name = service_name;
    observed.state = word;
    observed.raw_state = raw_state;
    observed.measured = true;

    if (!start_text.empty()) {
        int raw_start = 0;
        if (leading_int(start_text, raw_start)) {
            observed.start_type = start_type_for(raw_start);
        }
    }

    // THE START TYPE OUTRANKS THE STATE, and this is the one place the two are
    // combined rather than merely carried together. A service a person disabled
    // reports `SERVICE_STOPPED` exactly as a service that happened to stop does,
    // so a reader given only the state would see a candidate for recovery where
    // there is a decision to respect.
    if (observed.start_type == kStartDisabled) {
        observed.state = kStateManualStop;
    }
    return observed;
}

std::string windows_admission_for(const WindowsServiceObservation& observed,
                                  const std::string& unit) {
    (void)unit;
    if (!observed.measured) {
        // We did not understand the answer, so we do not know whether the
        // service is even installed — which is exactly what this word says on
        // the Linux side, and why the same spelling is used rather than a
        // second one.
        return nodecore::supervisor::kAdmitInstallStateUnknown;
    }
    if (observed.state == kStateNotInstalled) {
        return nodecore::supervisor::kAdmitNotInstalled;
    }
    if (observed.state == kStateManualStop) {
        // The Linux word for "a person switched it off". Reused deliberately: a
        // Fleet reader comparing a Linux host and a Windows host must see the
        // same refusal, not two words that mean one thing.
        return nodecore::supervisor::kAdmitMasked;
    }
    if (observed.state == kStateNeedsLogin) {
        return nodecore::supervisor::kAdmitNeedsLogin;
    }
    return nodecore::supervisor::kAdmitOk;
}

std::string windows_readiness_of(const WindowsServiceObservation& observed) {
    if (!observed.measured) {
        return kReadinessUnmeasured;
    }
    // The WEAK word, and the only one that counts as ready — the same rule
    // `readiness_of` applies to systemd's `active`. A Windows service that is
    // RUNNING may still have an unbound listener and a dead worker, so a
    // service that needs the stronger question declares a service-level probe.
    return observed.state == kStateRunning ? kReadinessReady : kReadinessNotReady;
}

}  // namespace nodecore::windows_core
