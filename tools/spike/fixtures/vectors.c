#include "vectors.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int vectors_read(const char *path, vectors_t *v) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return -1; }
    char magic[8];
    uint32_t hdr[4];
    if (fread(magic, 1, 8, f) != 8 || memcmp(magic, "SA2FXV1\0", 8) != 0 || fread(hdr, 4, 4, f) != 4) {
        fprintf(stderr, "%s: bad vector header\n", path); fclose(f); return -1;
    }
    v->va = hdr[0]; v->rows = hdr[1]; v->in_words = hdr[2]; v->out_words = hdr[3];
    size_t n = (size_t)v->rows * v->in_words;
    v->in = (uint32_t *)calloc(n ? n : 1, 4);
    v->out = (uint32_t *)calloc((size_t)v->rows * (2 + v->out_words) + 1, 4);
    if (fread(v->in, 4, n, f) != n) { fprintf(stderr, "%s: short rows\n", path); fclose(f); return -1; }
    fclose(f);
    return 0;
}

int vectors_write(const char *path, const vectors_t *v) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return -1; }
    uint32_t words = 2 + v->out_words;
    uint32_t hdr[3] = {v->va, v->rows, words};
    fwrite("SA2FXO1\0", 1, 8, f);
    fwrite(hdr, 4, 3, f);
    fwrite(v->out, 4, (size_t)v->rows * words, f);
    fclose(f);
    return 0;
}
