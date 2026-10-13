/* stream_audio_hash: digests of a recorder stream (host/sa2_stream.h), read as it is written.
 *
 * Subtitle: reads a stream from a file or FIFO and prints one JSON object: per record kind the
 * count, bytes and a digest of the whole records; per audio op the count; and the PCM the guest
 * handed the audio devices, by op: XAudio2 SubmitSourceBuffer (the CRI Sound Renderer's output,
 * interleaved by 0x007845C0) and DirectSound Unlock. Two runs whose digests are equal recorded the
 * same bytes. tools/s8/native_kernels/ab_game.py runs it beside a recorder run of the game.
 *
 *   stream_audio_hash STREAM [OUT.json]
 *
 * The digest is a 64-bit FNV-1a over 8-byte words (and the tail byte by byte), with the length
 * folded in: fast enough for the 13 GB a Metal Harbor replay records, and any byte that differs
 * changes it. The header's JSON describes the run (its paths among them), so it is counted, not
 * compared.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../../host/sa2_stream.h"

typedef struct { uint64_t h, bytes, count; } dig;

static void dig_init(dig *d) { d->h = 0xcbf29ce484222325ull; d->bytes = 0; d->count = 0; }
static void dig_add(dig *d, const uint8_t *p, size_t n) {
    uint64_t h = d->h;
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t w;
        memcpy(&w, p + i, 8);
        h = (h ^ w) * 0x100000001b3ull;
    }
    for (; i < n; i++) h = (h ^ p[i]) * 0x100000001b3ull;
    h = (h ^ (uint64_t)n) * 0x100000001b3ull;
    d->h = h;
    d->bytes += n;
}

static int read_full(FILE *f, void *buf, size_t n) { return fread(buf, 1, n, f) == n; }

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: stream_audio_hash STREAM [OUT.json]\n");
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 2; }
    static char iobuf[1 << 20];
    setvbuf(f, iobuf, _IOFBF, sizeof iobuf);
    uint8_t head[16];
    if (!read_full(f, head, 16) || memcmp(head, SA2S_MAGIC, 8)) {
        fprintf(stderr, "not a stream\n");
        return 1;
    }
    uint32_t version, header_bytes;
    memcpy(&version, head + 8, 4);
    memcpy(&header_bytes, head + 12, 4);
    for (uint32_t i = 16; i < header_bytes; i++)
        if (fgetc(f) == EOF) { fprintf(stderr, "short header\n"); return 1; }
    enum { KINDS = 7 };
    dig kind[KINDS], all, submit_pcm, unlock_pcm;
    for (int k = 0; k < KINDS; k++) dig_init(&kind[k]);
    dig_init(&all);
    dig_init(&submit_pcm);
    dig_init(&unlock_pcm);
    uint64_t ops[3][SA2S_AU_OP_COUNT] = {{0}}, records = 0, problems = 0, ended = 0;
    size_t cap = 1 << 20;
    uint8_t *rec = malloc(cap);
    sa2s_record_head rh;
    while (read_full(f, &rh, sizeof rh)) {
        if (rh.bytes < sizeof rh || rh.bytes % 4) { problems++; break; }
        const size_t n = rh.bytes - sizeof rh;
        if (n > cap) {
            while (cap < n) cap *= 2;
            rec = realloc(rec, cap);
        }
        if (!read_full(f, rec, n)) { problems++; break; }
        records++;
        const uint32_t k = rh.kind < KINDS ? rh.kind : 0;
        dig_add(&kind[k], (const uint8_t *)&rh, sizeof rh);
        dig_add(&kind[k], rec, n);
        kind[k].count++;
        dig_add(&all, rec, n);
        if (rh.kind == SA2S_END) ended = 1;
        if (rh.kind != SA2S_AUDIO || n < 24) continue;
        uint16_t api, op;
        memcpy(&api, rec, 2);
        memcpy(&op, rec + 2, 2);
        uint32_t argc;
        memcpy(&argc, rec + 20, 4);
        if (api < 3 && op < SA2S_AU_OP_COUNT) ops[api][op]++;
        size_t at = 24 + 4 * (size_t)argc + 4;       /* past args[] and eax */
        if (at + 4 > n) { problems++; continue; }
        uint32_t nblobs;
        memcpy(&nblobs, rec + at, 4);
        at += 4;
        for (uint32_t b = 0; b < nblobs; b++) {
            if (at + sizeof(sa2s_blob_head) > n) { problems++; break; }
            sa2s_blob_head bh;
            memcpy(&bh, rec + at, sizeof bh);
            at += sizeof bh;
            if (at + bh.len > n) { problems++; break; }
            if (bh.role == SA2S_BLOB_PCM) {
                if (api == SA2S_AU_XAUDIO2 && op == SA2S_AU_XA_SUBMIT) {
                    dig_add(&submit_pcm, rec + at, bh.len);
                    submit_pcm.count++;
                } else if (api == SA2S_AU_DSOUND && op == SA2S_AU_DS_UNLOCK) {
                    dig_add(&unlock_pcm, rec + at, bh.len);
                    unlock_pcm.count++;
                }
            }
            at += (bh.len + 3u) & ~3u;
        }
    }
    FILE *o = argc > 2 ? fopen(argv[2], "w") : stdout;
    if (!o) { perror(argv[2]); return 2; }
    static const char *const KN[KINDS] = {"unknown", "call", "d3dx", "frame", "note", "end", "audio"};
    fprintf(o, "{\"version\": %u, \"header_bytes\": %u, \"records\": %llu, \"ended\": %s, "
               "\"problems\": %llu, \"records_digest\": \"%016llx\", \"payload_bytes\": %llu, \"kinds\": {",
            version, header_bytes, (unsigned long long)records, ended ? "true" : "false",
            (unsigned long long)problems, (unsigned long long)all.h, (unsigned long long)all.bytes);
    for (int k = 0; k < KINDS; k++)
        fprintf(o, "%s\"%s\": {\"count\": %llu, \"bytes\": %llu, \"digest\": \"%016llx\"}", k ? ", " : "",
                KN[k], (unsigned long long)kind[k].count, (unsigned long long)kind[k].bytes,
                (unsigned long long)kind[k].h);
    fprintf(o, "}, \"xa_submit_pcm\": {\"count\": %llu, \"bytes\": %llu, \"digest\": \"%016llx\"}, "
               "\"ds_unlock_pcm\": {\"count\": %llu, \"bytes\": %llu, \"digest\": \"%016llx\"}, \"audio_ops\": {",
            (unsigned long long)submit_pcm.count, (unsigned long long)submit_pcm.bytes,
            (unsigned long long)submit_pcm.h, (unsigned long long)unlock_pcm.count,
            (unsigned long long)unlock_pcm.bytes, (unsigned long long)unlock_pcm.h);
    int first = 1;
    for (int a = 1; a < 3; a++)
        for (int p = 1; p < SA2S_AU_OP_COUNT; p++)
            if (ops[a][p]) {
                fprintf(o, "%s\"%d.%d\": %llu", first ? "" : ", ", a, p, (unsigned long long)ops[a][p]);
                first = 0;
            }
    fprintf(o, "}}\n");
    if (o != stdout) fclose(o);
    return problems ? 1 : 0;
}
