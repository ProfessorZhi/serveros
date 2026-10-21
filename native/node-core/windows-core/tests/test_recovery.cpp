#include "check.hpp"
#include "windows-core/recovery.hpp"
#include "windows-core/status_pipe.hpp"
using namespace nodecore::windows_core;
using namespace std::string_literals;
struct Tx : RecoveryTransaction {
    RecoveryState &s;
    bool &locked;
    bool persist = true;
    Tx(RecoveryState &v, bool &l) : s(v), locked(l) { locked = true; }
    ~Tx() { locked = false; }
    bool load(RecoveryState &out) override {
        out = s;
        return true;
    }
    bool save(const RecoveryState &in) override {
        if (!persist)
            return false;
        s = in;
        return true;
    }
};
struct Store : RecoveryStore {
    RecoveryState s;
    bool locked = false, refuse = false, persist = true;
    std::unique_ptr<RecoveryTransaction> lock(const UnitGrant &) override {
        if (locked || refuse)
            return {};
        auto tx = std::make_unique<Tx>(s, locked);
        tx->persist = persist;
        return tx;
    }
};
struct Backend : RecoveryBackend {
    Store &store;
    int starts = 0, probes = 0;
    bool effect = true;
    bool unknown = false;
    UnitState state{true, true, true, UnitCondition::Stopped};
    ProbeReadiness before = ProbeReadiness::NotReady, after = ProbeReadiness::Ready;
    Backend(Store &s) : store(s) {}
    UnitState inspect(const UnitGrant &) override { return state; }
    ProbeReadiness probe(const UnitGrant &) override {
        probes++;
        return starts ? after : before;
    }
    StartOutcome start(const UnitGrant &) override {
        CHECK(store.locked);
        CHECK(store.s.pending);
        starts++;
        return unknown  ? StartOutcome::Unknown
               : effect ? StartOutcome::Accepted
                        : StartOutcome::Refused;
    }
};
int main() {
    UnitGrant grant{UnitRole::CloudCli, UnitManager::Scm, "ServerOSCloudCLI", "S-1-5-21-100", true};
    CHECK(valid_grants({grant}));
    auto alias = grant;
    alias.role = UnitRole::Tunnel;
    alias.target = "serveroscloudcli";
    CHECK(!valid_grants({grant, alias}));
    auto bad = grant;
    bad.target = "foo\0bar"s;
    CHECK(!valid_grants({bad}));
    {
        Store s;
        Backend b(s);
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::Connector, 10000).code, std::string("NOT_GRANTED"));
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("RECOVERED"));
        CHECK_EQ(b.starts, 1);
        CHECK(!s.s.pending);
        CHECK_EQ(s.s.attempts.size(), std::size_t(1));
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10001).code, std::string("ALREADY_READY"));
    }
    for (auto condition : {UnitCondition::Disabled, UnitCondition::NeedsLogin, UnitCondition::Busy,
                           UnitCondition::Unknown, UnitCondition::Running}) {
        Store s;
        Backend b(s);
        b.state.condition = condition;
        RecoveryEngine e({grant}, s, b);
        CHECK(!e.recover(UnitRole::CloudCli, 10000).acted);
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        b.unknown = true;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("OUTCOME_UNKNOWN"));
        CHECK(s.s.pending);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 14000).code, std::string("OUTCOME_UNKNOWN"));
        CHECK_EQ(b.starts, 1);
    }
    for (int gate = 0; gate < 3; gate++) {
        Store s;
        Backend b(s);
        if (gate == 0)
            b.state.measured = false;
        if (gate == 1)
            b.state.native_grant = false;
        if (gate == 2)
            b.state.owner_matches = false;
        RecoveryEngine e({grant}, s, b);
        CHECK(!e.recover(UnitRole::CloudCli, 10000).acted);
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        b.before = ProbeReadiness::Unmeasured;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("PROBE_UNMEASURED"));
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        b.effect = false;
        b.after = ProbeReadiness::NotReady;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("NOT_RECOVERED"));
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10059).code, std::string("BACKOFF"));
        CHECK(e.recover(UnitRole::CloudCli, 10060).acted);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10359).code, std::string("BACKOFF"));
        CHECK(e.recover(UnitRole::CloudCli, 10360).acted);
        RecoveryEngine restarted({grant}, s, b);
        CHECK_EQ(restarted.recover(UnitRole::CloudCli, 11260).code, std::string("CIRCUIT_OPEN"));
        CHECK_EQ(b.starts, 3);
    }
    {
        Store s;
        Backend b(s);
        s.s.pending = true;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("OUTCOME_UNKNOWN"));
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        s.s.last_seen = 11000;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("CLOCK_UNTRUSTED"));
    }
    {
        Store s;
        Backend b(s);
        s.persist = false;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("PRECOMMIT_FAILED"));
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        s.refuse = true;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code,
                 std::string("WRITER_BUSY_OR_UNTRUSTED"));
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        s.s.last_seen = 10000;
        s.s.attempts = {9800, 9900, 10000};
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 12000).code, std::string("BUDGET_EXHAUSTED"));
        CHECK_EQ(b.starts, 0);
    }
    {
        Store s;
        Backend b(s);
        b.after = ProbeReadiness::Unmeasured;
        RecoveryEngine e({grant}, s, b);
        CHECK_EQ(e.recover(UnitRole::CloudCli, 10000).code, std::string("PROBE_UNMEASURED"));
        CHECK_EQ(s.s.failures, 0U);
        CHECK_EQ(s.s.attempts.size(), std::size_t(1));
    }
    {
        RecoveryState s;
        s.last_seen = 100;
        s.attempts = {1, 60};
        s.pending = true;
        s.failures = 2;
        auto encoded = encode_recovery_state(s);
        RecoveryState out;
        CHECK(decode_recovery_state(encoded, out));
        CHECK_EQ(out.attempts, s.attempts);
        CHECK(out.pending);
        CHECK(!decode_recovery_state(encoded + "junk", out));
        CHECK(!decode_recovery_state("", out));
    }
    {
        CHECK(valid_pipe_limits({}));
        CHECK(!valid_pipe_limits({0, 1, 1}));
        auto frame = encode_status_frame("status.v1");
        std::string payload;
        CHECK(decode_status_frame(frame, payload));
        CHECK_EQ(payload, std::string("status.v1"));
        CHECK(!decode_status_frame(frame + "x", payload));
        frame[0] = 127;
        CHECK(!decode_status_frame(frame, payload));
        CHECK(encode_status_frame(std::string(65537, 'x')).empty());
    }
    return check::report("windows_recovery_protocol");
}
