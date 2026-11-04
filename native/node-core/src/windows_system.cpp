// The Win32 calls behind the Windows collector. Compiled only under _WIN32.
//
// Read-only throughout: every call here is a query. Nothing opens a file or a
// volume handle, nothing enables a privilege, and nothing reads another
// process. A call that fails is reported with its GetLastError code by name in
// the reason, never as a zero.
#ifdef _WIN32

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <string>

#include "nodecore/windows_sample.hpp"

namespace nodecore {

namespace {

std::uint64_t to_u64(const FILETIME& value) {
    return (static_cast<std::uint64_t>(value.dwHighDateTime) << 32) | value.dwLowDateTime;
}

std::string error_text(const char* call) {
    return std::string(call) + " failed (GetLastError " + std::to_string(::GetLastError()) + ")";
}

std::wstring widen(const std::string& text) {
    if (text.empty()) {
        return std::wstring();
    }
    const int size = ::MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                                           nullptr, 0);
    std::wstring out(static_cast<std::size_t>(size > 0 ? size : 0), L'\0');
    if (size > 0) {
        ::MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(),
                              size);
    }
    return out;
}

std::string narrow(const wchar_t* text) {
    const int size = ::WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    if (size <= 1) {
        return std::string();
    }
    std::string out(static_cast<std::size_t>(size - 1), '\0');
    ::WideCharToMultiByte(CP_UTF8, 0, text, -1, out.data(), size, nullptr, nullptr);
    return out;
}

WindowsVolumeFacts read_volume(const std::string& role, const std::string& path) {
    WindowsVolumeFacts volume;
    volume.role = role;
    volume.path = path;
    const std::wstring wide_path = widen(path);

    ULARGE_INTEGER available{}, total{}, free{};
    if (::GetDiskFreeSpaceExW(wide_path.c_str(), &available, &total, &free)) {
        volume.space_ok = true;
        volume.total_bytes = total.QuadPart;
        volume.free_bytes = free.QuadPart;
        volume.available_bytes = available.QuadPart;
    } else {
        volume.space_reason = error_text("GetDiskFreeSpaceExW");
    }

    wchar_t mount[MAX_PATH + 1] = {};
    if (!::GetVolumePathNameW(wide_path.c_str(), mount, MAX_PATH)) {
        volume.volume_reason = error_text("GetVolumePathNameW");
        return volume;
    }
    volume.mount_point = narrow(mount);

    wchar_t name[MAX_PATH + 1] = {};
    if (::GetVolumeNameForVolumeMountPointW(mount, name, MAX_PATH)) {
        volume.volume_name = narrow(name);
    } else {
        // A mapped network drive has no local volume GUID. Its mount point is
        // still the honest source.
        volume.volume_name = volume.mount_point;
    }

    wchar_t fs_name[MAX_PATH + 1] = {};
    if (::GetVolumeInformationW(mount, nullptr, 0, nullptr, nullptr, nullptr, fs_name, MAX_PATH)) {
        volume.fstype = narrow(fs_name);
    } else {
        volume.fstype = "unknown";
    }
    volume.volume_ok = true;
    return volume;
}

}  // namespace

WindowsFacts read_windows_facts(const std::vector<std::pair<std::string, std::string>>& roles) {
    WindowsFacts facts;

    FILETIME idle{}, kernel{}, user{};
    if (::GetSystemTimes(&idle, &kernel, &user)) {
        facts.cpu_ok = true;
        facts.idle_ticks = to_u64(idle);
        facts.kernel_ticks = to_u64(kernel);
        facts.user_ticks = to_u64(user);
    } else {
        facts.cpu_reason = error_text("GetSystemTimes");
    }

    MEMORYSTATUSEX memory{};
    memory.dwLength = sizeof(memory);
    if (::GlobalMemoryStatusEx(&memory)) {
        facts.memory_ok = true;
        facts.total_physical_bytes = memory.ullTotalPhys;
        facts.available_physical_bytes = memory.ullAvailPhys;
    } else {
        facts.memory_reason = error_text("GlobalMemoryStatusEx");
    }

    facts.tick_ok = true;
    facts.tick_milliseconds = ::GetTickCount64();

    for (const auto& [role, path] : roles) {
        facts.volumes.push_back(read_volume(role, path));
    }
    return facts;
}

}  // namespace nodecore

#endif  // _WIN32
