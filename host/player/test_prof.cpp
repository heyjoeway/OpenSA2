// test_prof.cpp: the player's sampler (prof.h) on a workload whose split is known.
//
// Built and run by Documentation/scripts/tests/test_player_prof.py, which reads the files with
// tools/s8/player_prof.py and checks what they say.
//
//   test_prof run DIR       starts the sampler from SA2_PLAYER_PROF into DIR, burns about twice
//                           as much CPU in burn_call (zone "call", by way of burn_leaf) as in
//                           burn_stream (zone "stream"), marks 30 Presents, stops; prints the
//                           sampler's lines
//   test_prof killed DIR    the same, then ends with SIGKILL before stop (a session the system
//                           killed): the samples already taken stay in the file
//   test_prof refused DIR   start_from_env with an output rule that refuses: says why, writes nothing
//   test_prof unset DIR     start_from_env with SA2_PLAYER_PROF unset: says nothing, writes nothing
//   test_prof cost DIR      CPU seconds of a fixed workload, with the sampler on (into DIR) when
//                           SA2_PLAYER_PROF is set (the overhead check)
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "prof.h"

namespace {

volatile uint64_t g_sink;

// The leaf: a dependent chain the compiler cannot fold, so the CPU time is spent here.
__attribute__((noinline)) uint64_t burn_leaf(uint64_t rounds, uint64_t x) {
    for (uint64_t i = 0; i < rounds; i++) x = x * 6364136223846793005ull + 1442695040888963407ull + (x >> 29);
    return x;
}

double cpu_s() {
    timespec ts{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return double(ts.tv_sec) + double(ts.tv_nsec) / 1e9;
}

// Burns `seconds` of this thread's CPU in burn_leaf, called from here.
__attribute__((noinline)) void burn_call(double seconds) {
    const double end = cpu_s() + seconds;
    uint64_t x = 1;
    while (cpu_s() < end) x = burn_leaf(200000, x);
    g_sink = g_sink + x;
}

// The same, in a loop of its own (no call): its samples' leaf is burn_stream.
__attribute__((noinline)) void burn_stream(double seconds) {
    const double end = cpu_s() + seconds;
    uint64_t x = 3;
    while (cpu_s() < end)
        for (int i = 0; i < 200000; i++) x = x * 2862933555777941757ull + 3037000493ull + (x >> 31);
    g_sink = g_sink + x;
}

std::string allow(const std::string&) { return ""; }
std::string refuse(const std::string& path) { return path + " is not allowed here"; }

int workload(bool kill_before_stop) {
    const std::string line = sa2prof::start_from_env("", allow);
    std::printf("%s\n", line.c_str());
    if (!sa2prof::running()) return 3;
    for (uint32_t f = 1; f <= 30; f++) {
        sa2prof::zone(sa2prof::kZoneCall);
        burn_call(0.04);
        sa2prof::zone(sa2prof::kZoneStream);
        burn_stream(0.02);
        sa2prof::frame(f);
    }
    sa2prof::zone(sa2prof::kZoneOther);
    std::fflush(stdout);
    if (kill_before_stop) std::raise(SIGKILL);
    std::printf("%s\n", sa2prof::stop().c_str());
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: test_prof run|killed|refused|unset|cost DIR\n");
        return 2;
    }
    const std::string mode = argv[1];
    setenv("SA2_PLAYER_PROF_OUT", argv[2], 1);
    if (mode == "cost") {
        const std::string line = sa2prof::start_from_env("", allow);
        const double t0 = cpu_s();
        uint64_t x = 5;
        for (long i = 0; i < 1500; i++) x = burn_leaf(1000000, x);
        g_sink = x;
        const double used = cpu_s() - t0;
        const std::string stopped = sa2prof::stop();
        std::printf("cpu_s %.6f\n%s\n%s\n", used, line.c_str(), stopped.c_str());
        return 0;
    }
    if (mode == "run") return workload(false);
    if (mode == "killed") return workload(true);
    if (mode == "refused") {
        std::printf("%s\n", sa2prof::start_from_env("", refuse).c_str());
        return sa2prof::running() ? 1 : 0;
    }
    if (mode == "unset") {
        unsetenv("SA2_PLAYER_PROF");
        std::printf("[%s]\n", sa2prof::start_from_env("", allow).c_str());
        return sa2prof::running() ? 1 : 0;
    }
    return 2;
}
