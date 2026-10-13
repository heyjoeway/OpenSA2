// sa2_cpu_plan.h: where the Android session puts the game's guest threads and the player's thread,
// and the clocks its perf line reads on them (sa2_android_session.cpp, "Speed on a phone").
// Header-only and free of Android's headers, so
// Documentation/scripts/tests/test_android_app.py compiles it on the host over a fake sysfs.
#pragma once

#include <sched.h>

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace sa2cpu {

// A CPU list as the runtime's SA2_GUEST_HOST_CPUS reads it (runtime/sa2_threads.c: "6,7",
// "0-5"): false when `s` is not one.
inline bool parse_list(const std::string& s, cpu_set_t& set) {
    CPU_ZERO(&set);
    const char* p = s.c_str();
    while (*p) {
        char* end = nullptr;
        const long a = std::strtol(p, &end, 10);
        if (end == p || a < 0 || a >= CPU_SETSIZE) return false;
        long b = a;
        p = end;
        if (*p == '-') {
            b = std::strtol(p + 1, &end, 10);
            if (end == p + 1 || b < a || b >= CPU_SETSIZE) return false;
            p = end;
        }
        for (long c = a; c <= b; c++) CPU_SET(int(c), &set);
        if (*p == ',') p++;
        else if (*p) return false;
    }
    return CPU_COUNT(&set) > 0;
}

// Ascending CPUs as a list in that grammar, runs as ranges: {0,1,2,3,4,5} is "0-5".
inline std::string list_of(const std::vector<int>& cpus) {
    std::string out;
    for (size_t i = 0; i < cpus.size();) {
        size_t j = i;
        while (j + 1 < cpus.size() && cpus[j + 1] == cpus[j] + 1) j++;
        if (!out.empty()) out += ",";
        out += std::to_string(cpus[i]);
        if (j > i) out += "-" + std::to_string(cpus[j]);
        i = j + 1;
    }
    return out;
}

// The number a sysfs file holds, -1 when it does not read.
inline long read_number(const std::string& path) {
    std::ifstream f(path);
    long v = -1;
    if (!(f >> v)) return -1;
    return v;
}

struct Plan {
    std::string guest, player;   // CPU lists, both empty when nothing is placed
    std::string why;             // what was read, for the session's report
};

// Of the `allowed` CPUs, ranked by <root>/cpuN/cpufreq/cpuinfo_max_freq (or, when no CPU has one,
// cpu_capacity): the guest gets one CPU of the top tier, the highest numbered (its threads run one
// at a time under the guest lock, so one CPU loses nothing), and the player another CPU of the top
// tier when it has two or more, else the whole next tier. The user's phone of 2026-10-12 is why:
// with the player on a prime core its render thread cost 6.1 ms a frame in Wild Canyon, against
// 11-13 ms spread over the six performance cores, whose shared clock the governor kept low. When
// every CPU that reads tops out alike, or none reads, nothing is placed. `root` is
// /sys/devices/system/cpu on a device.
inline Plan plan(const std::string& root, const cpu_set_t& allowed) {
    Plan p;
    for (const char* kind : {"cpufreq/cpuinfo_max_freq", "cpu_capacity"}) {
        std::map<long, std::vector<int>> by;     // the value read -> the CPUs that read it
        for (int c = 0; c < CPU_SETSIZE; c++) {
            if (!CPU_ISSET(c, &allowed)) continue;
            const long v = read_number(root + "/cpu" + std::to_string(c) + "/" + kind);
            if (v > 0) by[v].push_back(c);
        }
        if (by.empty()) continue;
        p.why = kind;
        for (const auto& [v, cpus] : by) p.why += " " + std::to_string(v) + " on " + list_of(cpus) + ";";
        if (by.size() < 2) {
            p.why += " every CPU tops out alike, so nothing is placed";
            return p;
        }
        const std::vector<int>& top = by.rbegin()->second;      // ascending CPU numbers
        p.guest = list_of({top.back()});
        p.player = top.size() >= 2 ? list_of({top[top.size() - 2]}) : list_of(std::next(by.rbegin())->second);
        p.why += " the guest on " + p.guest + ", the player on " + p.player;
        return p;
    }
    p.why = "no CPU's cpuinfo_max_freq or cpu_capacity reads, so nothing is placed";
    return p;
}

// The current clock of each CPU of `cpus` (a list as parse_list reads it), for the session's
// perf line: "N:MHz" words in CPU order, from <root>/cpuN/cpufreq/scaling_cur_freq (kHz, rounded
// to the nearest MHz), "N:?" for a CPU whose file does not read; empty for a list that does not
// read. "6-7" on the user's phone at full clock is "6:4320 7:4320".
inline std::string clocks_mhz(const std::string& root, const std::string& cpus) {
    cpu_set_t set;
    if (!parse_list(cpus, set)) return "";
    std::string out;
    for (int c = 0; c < CPU_SETSIZE; c++) {
        if (!CPU_ISSET(c, &set)) continue;
        const long khz = read_number(root + "/cpu" + std::to_string(c) + "/cpufreq/scaling_cur_freq");
        out += (out.empty() ? "" : " ") + std::to_string(c) + ":" +
               (khz > 0 ? std::to_string((khz + 500) / 1000) : std::string("?"));
    }
    return out;
}

}  // namespace sa2cpu
