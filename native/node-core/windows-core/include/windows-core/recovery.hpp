#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
namespace nodecore::windows_core {
enum class UnitRole { CloudCli, Connector, Tunnel };
enum class UnitManager { Scm, TaskScheduler };
enum class UnitCondition { Running, Stopped, Disabled, NeedsLogin, Busy, Unknown };
enum class ProbeReadiness { Ready, NotReady, Unmeasured };
enum class StartOutcome { Accepted, Refused, Unknown };
// Trusted launcher configuration, never accepted from status IPC.
struct UnitGrant {
    UnitRole role;
    UnitManager manager;
    std::string target;
    std::string owner_sid;
    bool enabled = false;
};
std::string unit_key(const UnitGrant &);
bool valid_grants(const std::vector<UnitGrant> &);
struct UnitState {
    bool measured = false, native_grant = false, owner_matches = false;
    UnitCondition condition = UnitCondition::Unknown;
};
struct RecoveryState {
    std::int64_t last_seen = 0, circuit_until = 0;
    std::vector<std::int64_t> attempts;
    unsigned failures = 0;
    bool pending = false;
};
std::string encode_recovery_state(const RecoveryState &);
bool decode_recovery_state(const std::string &, RecoveryState &);
class RecoveryTransaction {
  public:
    virtual ~RecoveryTransaction() = default;
    virtual bool load(RecoveryState &) = 0;
    // Must durably commit BEFORE returning true. A failure fences the action.
    virtual bool save(const RecoveryState &) = 0;
};
class RecoveryStore {
  public:
    virtual ~RecoveryStore() = default;
    // Nonblocking exclusive per-native-target writer, held by returned object.
    virtual std::unique_ptr<RecoveryTransaction> lock(const UnitGrant &) = 0;
};
class RecoveryBackend {
  public:
    virtual ~RecoveryBackend() = default;
    virtual UnitState inspect(const UnitGrant &) = 0;
    virtual ProbeReadiness probe(const UnitGrant &) = 0;
    // Native implementation rechecks grants/state and performs only a local
    // start.
    virtual StartOutcome start(const UnitGrant &) = 0;
};
struct RecoveryResult {
    bool acted = false;
    std::string code;
};
class RecoveryEngine {
  public:
    RecoveryEngine(std::vector<UnitGrant>, RecoveryStore &, RecoveryBackend &);
    RecoveryResult recover(UnitRole, std::int64_t wall_epoch);

  private:
    std::vector<UnitGrant> grants_;
    RecoveryStore &store_;
    RecoveryBackend &backend_;
    bool valid_;
};
// Native adapter uses actual local SCM/COM rights and registered identity. A
// probe is REQUIRED from the trusted owner and cannot be replaced by RUNNING.
class FunctionalProbe {
  public:
    virtual ~FunctionalProbe() = default;
    virtual ProbeReadiness check(UnitRole) = 0;
};
class Win32RecoveryBackend final : public RecoveryBackend {
  public:
    explicit Win32RecoveryBackend(FunctionalProbe &p) : probe_(p) {}
    UnitState inspect(const UnitGrant &) override;
    ProbeReadiness probe(const UnitGrant &g) override { return probe_.check(g.role); }
    StartOutcome start(const UnitGrant &) override;

  private:
    FunctionalProbe &probe_;
};
class Win32RecoveryStore final : public RecoveryStore {
  public:
    std::unique_ptr<RecoveryTransaction> lock(const UnitGrant &) override;
};
} // namespace nodecore::windows_core
