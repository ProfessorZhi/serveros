#pragma once
#include "windows-core/recovery.hpp"
#include "windows-core/status_document.hpp"
#include <atomic>
#include <memory>
namespace nodecore::windows_core {
struct HttpProbe {
    unsigned port = 0;
    std::string path, key, value;
    bool boolean_value = false, is_boolean = false;
};
struct WatchUnit {
    UnitGrant grant;
    std::string desired;
    HttpProbe probe;
};
struct WatchConfig {
    std::string node, principal;
    std::int64_t generation = 0;
    bool observe_only = true;
    unsigned interval_ms = 5000;
    std::vector<WatchUnit> services;
};
bool parse_watch_config(const std::string &, const std::string &actual_sid, WatchConfig &,
                        std::string &error);
std::vector<WindowsRuntimeRow> watch_runtime_pass(const WatchConfig &, RecoveryStore &,
                                                  RecoveryBackend &, std::int64_t now);
ProbeReadiness parse_http_probe(const std::string &, const HttpProbe &);
class LoopbackFunctionalProbe final : public FunctionalProbe {
  public:
    explicit LoopbackFunctionalProbe(const WatchConfig &c) : config_(c) {}
    ProbeReadiness check(UnitRole) override;

  private:
    const WatchConfig &config_;
};
// Fixed OS LocalAppData root; singleton writer and owner-only documents.
class LocalWatchState {
  public:
    virtual ~LocalWatchState() = default;
    virtual std::string owner_sid() const = 0;
    virtual bool read_desired(std::string &) = 0;
    virtual bool commit_generation(std::int64_t, const std::string &exact_config) = 0;
    virtual bool publish(const std::string &snapshot) = 0;
};
std::unique_ptr<LocalWatchState> open_local_watch_state();
int run_windows_watch(std::atomic<bool> &stop);
int run_windows_service();
bool publish_windows_desired(const std::string &);
} // namespace nodecore::windows_core
