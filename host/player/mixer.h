// mixer.h: the player's sound (S5, design 3.5: "dsound and the CRI ADX path recorded
// as decoded PCM streams and cue events; the host plays them"). Renders the audio a
// version 2 stream records (host/sa2_stream.h, SA2S_AUDIO) as 48 kHz stereo float,
// placed in time by the runtime's own clock, so the sound a frame carries is the
// sound the guest's devices had played by that frame's virtual time.
//
// What it plays, and by which clock
// ---------------------------------
// The runtime's devices play nothing: runtime/sa2_dsound.c answers every cursor
// from the virtual clock and the buffer's declared rate, and runtime/sa2_xaudio2.c
// consumes each source voice's queue at its rate from an anchor. The recorder copies,
// after every call that changes a buffer or a voice, the device's own record of it
// (sa2s_ds_state, sa2s_xa_state), and the bytes the guest gives each one. Between two
// records nothing changes but the clock, so this mixer plays each source by the
// device's own formula from the device's own anchors:
//
//   DirectSound buffer:  byte position at virtual time t (in microseconds)
//       pos(t) = anchor_pos + (t - anchor_us) * avg_bytes_per_sec / 1e6
//     the runtime's cursor is floor(pos) rounded down to block_align; a looping
//     buffer wraps, any other halts at its end (runtime/sa2_dsound.c,
//     buf_play_cursor). avg_bytes_per_sec is the format's, or SetFrequency's
//     frequency times block_align: the bytes play at the frequency's rate.
//   XAudio2 source voice: frame position at virtual time t
//       played(t) = anchor_frame + (t - anchor_us) * samples_per_sec * ratio / 1e6
//     while it runs with buffers queued, at most queued_end (runtime/sa2_xaudio2.c,
//     frames_after: the ratio's float32 bits are an exact fraction, so the mixer
//     uses the same integers).
//
// Output sample n of the mix stands at virtual time origin + n / 48000 s, where the
// origin is the first record the mixer saw (an AUDIO record or a FRAME). All time
// arithmetic is integer, in units of 1/6 microsecond, in which output sample n is at
// 6 * origin_us + 125 n exactly; a source's position at a sample is an exact integer
// part and a fraction. A source whose frame rate is 48000 exactly plays the frame its
// integer part names (no interpolation: the bytes the cursor passes, one per output
// sample); any other is resampled linearly between that frame and the next (a looping
// buffer's next after its last is its first; a non-looping buffer's, and a voice's at
// the end of what it was given, is the same frame). Linear interpolation is the
// documented choice: it adds no latency and needs no history. Its error is a mild
// high-frequency roll-off and, from a source rate below 48 kHz, images of the band
// above the source's own Nyquist frequency folded back in (aliasing), which it does
// not filter; it matters most for CRI's 22.05 kHz and 32 kHz buffers (88200 and
// 128000 bytes per second after SetFrequency in Metal Harbor).
//
// Levels
// ------
//   DirectSound volume, hundredths of a decibel (DSBVOLUME_MIN -10000 .. 0):
//       gain = 10^(volume / 2000), and 0 at -10000
//   DirectSound pan (-10000 left .. 10000 right): the other channel is attenuated by
//       |pan| hundredths of a decibel and the near one is left at full level, as
//       DirectSound documents it
//   XAudio2: the source voice's level matrix, level[dst * SourceChannels + src], as
//       SetOutputMatrix gave it. A voice of more than two channels with none is
//       mixed by a documented fallback and counted (default_matrix_samples): six
//       channels as 5.1, L = FL + 0.7071 (FC + BL), R = FR + 0.7071 (FC + BR), LFE
//       dropped; any other count, its first two channels. Then SetVolume's level, 1
//       until one is recorded (the runtime refuses SetVolume today)
// The two paths are summed; a mixed sample outside [-1, 1] is counted
// (clipped_samples) and clamped. A duplicate buffer starts at volume and pan 0, as
// the runtime models it (host/sa2_stream.h), not at its original's, as DirectSound
// does.
//
// The stores
// ----------
// A buffer plays its store, rebuilt from the PCM blobs of the Unlocks (each at its
// pointer less the STATE's mem), kept by the store's guest address so a duplicate that
// shares its original's store plays the same bytes, and made zero-filled at its
// buffer's length the first time a STATE names it, as the runtime's Lock makes it.
// Locks are counted per store; the Unlock that closes a store's last one carries the
// recorder's FNV-1a of the guest's whole store (store_hash), and the mixer's copy must
// hash the same (store_checks, store_mismatches).
//
// Every such Unlock is hashed unless the caller asks for every n-th
// (set_store_check_every): a live session's player does (player.cpp,
// kLiveStoreCheckEvery), because the hash is one byte at a time over the whole store,
// and hashing every one was 6.4% of a live player's samples in a stage (apply_ds, the
// thermal unit's profile of 2026-10-12) for a check whose verdict changes nothing that
// plays. Its recorder hashes one closing Unlock in 64 for the same reason
// (host/sa2_present_rec.c, SA2_REC_STORE_HASH_EVERY: 0.20 ms a tick of the guest's
// serialized path on the PC), and carries no store_hash on the others. So with n > 1 the
// mixer holds the first closing Unlock that carries a store_hash, then each one that
// carries it at least n closing Unlocks (counted across stores) after the last it held,
// and counts every other closing Unlock, one without a store_hash among them, as not
// hashed (store_check_every, stores_not_hashed): a stream whose every Unlock carries the
// hash is held at every n-th, one whose recorder hashed every 64th at each hash it
// carries. With n = 1 (replays and the renderer) every closing Unlock is held, and one
// that carries no store_hash is a mismatch. A HASH that is not 8 bytes is a mismatch
// whatever n is.
//
// What it checks, and what is a problem
// -------------------------------------
// Every DS_GET_POSITION, DS_GET_STATUS and XA_GET_STATE record carries the answer the
// device gave the guest; the mixer predicts each from the source's previous record and
// the clock, and a prediction that differs is a problem (cursor_mismatches,
// status_mismatches, state_mismatches): the mixer would be playing by another clock
// than the guest's. Also problems: a record of an op the header's table does not name
// or with blobs its line does not give, a call on a buffer or voice no record made, an
// Unlock's bytes outside its buffer's store, a store that does not hash to the guest's
// (or an Unlock that should carry the hash and does not, but in a live session's mixer:
// "The stores"), a store whose length is not its buffer's, a submit whose length is not
// whole frames, a playing buffer of a format
// the mixer does not decode (anything but 8- or 16-bit PCM and 32-bit float), a record
// earlier than the one before it (late_records). Not problems, counted: samples a
// playing buffer had no store for (samples_without_store, which a caller should still
// read as a finding: the runtime's buffer played bytes the mixer never saw), samples a
// running voice had nothing queued for (starved_samples).
#pragma once

#include "sa2_stream.h"
#include "stream.h"

#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <set>
#include <string>
#include <vector>

// Peak and RMS of a path, over every output sample of both channels.
struct Sa2Level {
    double peak = 0, sumsq = 0;
    uint64_t samples = 0;
    void add(float l, float r);
    double rms() const;
    std::string json() const;          // {"peak": .., "rms": .., "peak_dbfs": .., "rms_dbfs": ..}
};

class Sa2Mixer {
public:
    static constexpr uint32_t kRate = 48000, kChannels = 2;

    // An SA2S_AUDIO record, in stream order: the mix is rendered up to its time,
    // then the record is applied.
    void on_audio(const Sa2Record& r, const Sa2StreamReader& rd);
    // A FRAME: the mix is rendered up to its time.
    void on_frame(uint64_t virtual_us);
    // The samples rendered since the last clear_block(), interleaved L R, clamped (or limited).
    const std::vector<float>& block() const { return block_; }
    void clear_block() { block_.clear(); }

    bool started() const { return started_; }
    uint64_t origin_us() const { return origin_us_; }
    uint64_t samples() const { return n_; }
    uint64_t problems() const { return problems_; }
    const std::vector<std::string>& first_problems() const { return first_problems_; }
    void problem(const std::string& what);      // a problem the caller found (a stream with no audio)
    // A live session's: hash every n-th store an Unlock closes, and take a close without a
    // store_hash as not hashed (see "The stores"); 0 is taken as 1, which holds every one.
    void set_store_check_every(uint32_t n) { store_check_every_ = n ? n : 1u; }
    // Live play: a limiter in place of the clamp (mixer.cpp, "The limiter"). Off by default,
    // so every WAV a receipt reads is the clamped mix.
    void set_limiter(bool on) { limiter_ = on; }
    // The counters as the report's "audio" object members (no braces): records,
    // samples, per path, problems.
    std::string json_fields() const;

private:
    struct DsBuf {
        sa2s_ds_state st{};
        int32_t volume = 0, pan = 0;
        bool halted_seen = false;
    };
    struct XaBuf {
        uint64_t start = 0, end = 0;   // stream frames [start, end)
        std::vector<float> data;       // end - start frames of channels floats
    };
    struct XaVoice {
        sa2s_xa_state st{};
        std::deque<XaBuf> queue;
        uint32_t volume_bits = 0x3F800000u;   // 1.0f
    };

    void start(uint64_t virtual_us);
    void render_to(uint64_t virtual_us);
    void render_ds(DsBuf& b, uint64_t n0, uint64_t n1, std::vector<float>& acc);
    void render_xa(XaVoice& v, uint64_t n0, uint64_t n1, std::vector<float>& acc);
    bool ds_frame(const DsBuf& b, const std::vector<uint8_t>& store, uint64_t frame, float* l, float* r);
    bool ds_state(const Sa2Record& r, int arg, sa2s_ds_state* out);
    bool xa_state(const Sa2Record& r, int arg, sa2s_xa_state* out);
    void apply_ds(const Sa2Record& r, const Sa2AudioRow* row);
    void apply_xa(const Sa2Record& r, const Sa2AudioRow* row);
    std::vector<uint8_t>* store_of(const Sa2Record& r, const sa2s_ds_state& st);
    std::string where(const Sa2Record& r) const;

    bool started_ = false;
    uint64_t origin_us_ = 0, n_ = 0, last_us_ = 0;
    std::vector<float> block_, ds_acc_, xa_acc_;
    std::map<uint32_t, DsBuf> ds_;
    std::map<uint32_t, std::vector<uint8_t>> stores_;    // by the backing store's guest address
    std::map<uint32_t, uint32_t> store_open_;            // Locks open on each store
    std::set<uint32_t> bad_stores_;                      // stores whose length is not their buffer's
    std::map<uint32_t, XaVoice> xa_;
    std::map<std::string, uint64_t> by_op_;
    uint64_t records_ = 0;
    // DirectSound
    uint64_t ds_patches_ = 0, ds_patch_bytes_ = 0, ds_cursor_checks_ = 0, ds_cursor_mismatches_ = 0;
    uint64_t ds_store_checks_ = 0, ds_store_mismatches_ = 0;
    uint32_t store_check_every_ = 1;
    uint64_t ds_store_closes_ = 0, ds_store_unhashed_ = 0;   // closing Unlocks; those not held to a hash
    uint64_t ds_store_checked_at_ = 0;                       // the closing Unlock last held (0-based)
    uint64_t ds_status_checks_ = 0, ds_status_mismatches_ = 0, ds_no_store_samples_ = 0;
    uint64_t ds_primary_ = 0, ds_unsupported_samples_ = 0, ds_resampled_samples_ = 0;
    Sa2Level ds_level_;
    // XAudio2
    uint64_t xa_submits_ = 0, xa_submit_bytes_ = 0, xa_flushed_frames_ = 0, xa_state_checks_ = 0;
    uint64_t xa_state_mismatches_ = 0, xa_starved_samples_ = 0, xa_default_matrix_samples_ = 0;
    uint64_t xa_resampled_samples_ = 0;
    Sa2Level xa_level_, mix_level_;
    uint64_t clipped_ = 0, late_records_ = 0;
    bool limiter_ = false;
    float lim_gain_ = 1.0f, lim_least_ = 1.0f;
    uint64_t limited_ = 0;
    uint64_t problems_ = 0;
    std::vector<std::string> first_problems_;
};

// A WAV file of the mix: WAVE_FORMAT_IEEE_FLOAT, 2 channels, 48000 Hz, 32-bit,
// written as the samples come and its sizes patched at every flush and at close, so
// a run that dies leaves a WAV of what it had written at its last flush.
class Sa2Wav {
public:
    ~Sa2Wav() { close(); }
    bool open(const std::string& path, std::string* err);
    bool write(const float* interleaved, size_t frames);
    bool flush();                      // patch the sizes and flush
    bool close();
    uint64_t frames() const { return frames_; }
    uint64_t bytes() const { return 58u + frames_ * 8u; }
    bool failed() const { return failed_; }
private:
    FILE* f_ = nullptr;
    uint64_t frames_ = 0;
    bool failed_ = false;
};
