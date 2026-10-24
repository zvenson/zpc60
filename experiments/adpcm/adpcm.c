/* zpc60 experiment: 4-bit IMA ADPCM for the sample memory (a third of 12 bit), in blocks so a voice can start
 * anywhere (TRUNC) and play backwards. Block: int16 predictor, uint8 step index, 1 spare byte, then BLK nibbles.
 * The decoder is the firmware's: integer, no division, ~10 ops a sample. Two encoders, the same decoder:
 *   greedy  the standard one (the closest code now)
 *   look    tries every code pair for this and the next sample, keeps the first of the best pair
 * usage: adpcm enc|look BLK in.s16 out.adp | adpcm dec BLK in.adp n out.s16 | adpcm bench BLK n */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

static const int16_t STEP[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767};
static const int8_t IDX[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

/* one step of the decoder: the state (p, i) and a code -> the new p */
static inline void step(int32_t *p, int32_t *i, uint32_t c)
{
    int32_t s = STEP[*i], d = s >> 3;
    if (c & 4) d += s;
    if (c & 2) d += s >> 1;
    if (c & 1) d += s >> 2;
    *p += (c & 8) ? -d : d;
    if (*p > 32767) *p = 32767;
    if (*p < -32768) *p = -32768;
    *i += IDX[c];
    if (*i < 0) *i = 0;
    if (*i > 88) *i = 88;
}

static int IDX5[16] = {-1, -1, -1, -1, -1, -1, -1, -1, 1, 2, 3, 4, 5, 6, 7, 8};
/* 5 bits: sign + a magnitude 0..15, d = step * (2m + 1) / 16 */
static inline void step5(int32_t *p, int32_t *i, uint32_t c)
{
    int32_t s = STEP[*i], m = (int32_t)(c & 15u), d = (s * (2 * m + 1)) >> 4;
    *p += (c & 16) ? -d : d;
    if (*p > 32767) *p = 32767;
    if (*p < -32768) *p = -32768;
    *i += IDX5[m];
    if (*i < 0) *i = 0;
    if (*i > 88) *i = 88;
}
static uint32_t bytes5(uint32_t n, uint32_t blk) { return (n + blk - 1) / blk * (4 + blk * 5 / 8); }
static void encode5(const int16_t *x, uint32_t n, uint32_t blk, uint8_t *out)
{
    int32_t p = 0, i = 0;
    uint32_t k, o = 0, acc = 0, nb = 0;
    for (k = 0; k < n; k++) {
        uint32_t c, a, b;
        int64_t be = -1;
        if (k % blk == 0) {
            if (nb) { out[o++] = (uint8_t)acc; acc = nb = 0; }
            out[o++] = (uint8_t)(p & 255); out[o++] = (uint8_t)((p >> 8) & 255); out[o++] = (uint8_t)i; out[o++] = 0;
        }
        c = 0;
        for (a = 0; a < 32; a++) {                    /* two ahead, as "look" */
            int32_t q = p, j = i;
            int64_t e1;
            step5(&q, &j, a);
            e1 = (int64_t)(x[k] - q) * (x[k] - q);
            if (k + 1 >= n || (k + 1) % blk == 0) { if (be < 0 || e1 < be) { be = e1; c = a; } continue; }
            for (b = 0; b < 32; b++) {
                int32_t q2 = q, j2 = j;
                int64_t e;
                step5(&q2, &j2, b);
                e = e1 + (int64_t)(x[k + 1] - q2) * (x[k + 1] - q2);
                if (be < 0 || e < be) { be = e; c = a; }
            }
        }
        step5(&p, &i, c);
        acc |= c << nb; nb += 5;
        while (nb >= 8) { out[o++] = (uint8_t)acc; acc >>= 8; nb -= 8; }
    }
    if (nb) out[o++] = (uint8_t)acc;
}
static void decode5(const uint8_t *in, uint32_t n, uint32_t blk, int16_t *y)
{
    uint32_t k, o = 0, acc = 0, nb = 0;
    int32_t p = 0, i = 0;
    for (k = 0; k < n; k++) {
        uint32_t c;
        if (k % blk == 0) { nb = 0; acc = 0; p = (int16_t)(in[o] | in[o + 1] << 8); i = in[o + 2]; o += 4; }
        while (nb < 5) { acc |= (uint32_t)in[o++] << nb; nb += 8; }
        c = acc & 31u; acc >>= 5; nb -= 5;
        step5(&p, &i, c);
        y[k] = (int16_t)p;
    }
}

static uint32_t bytes_of(uint32_t n, uint32_t blk) { return (n + blk - 1) / blk * (4 + blk / 2); }

static uint32_t best_code(int32_t p, int32_t i, int32_t x)
{
    uint32_t c, b = 0;
    int64_t be = -1;
    for (c = 0; c < 16; c++) {
        int32_t q = p, j = i;
        int64_t e;
        step(&q, &j, c);
        e = (int64_t)(x - q) * (x - q);
        if (be < 0 || e < be) { be = e; b = c; }
    }
    return b;
}

static void encode(const int16_t *x, uint32_t n, uint32_t blk, uint8_t *out, int look)
{
    int32_t p = 0, i = 0;
    uint32_t k, o = 0;
    for (k = 0; k < n; k++) {
        uint32_t c;
        if (k % blk == 0) {                       /* a block's header: the state as it is */
            out[o++] = (uint8_t)(p & 255); out[o++] = (uint8_t)((p >> 8) & 255); out[o++] = (uint8_t)i; out[o++] = 0;
        }
        if (!look || k + 1 >= n || (k + 1) % blk == 0) c = best_code(p, i, x[k]);
        else {
            uint32_t a, b;
            int64_t be = -1;
            c = 0;
            for (a = 0; a < 16; a++) {
                int32_t q = p, j = i;
                int64_t e1;
                step(&q, &j, a);
                e1 = (int64_t)(x[k] - q) * (x[k] - q);
                for (b = 0; b < 16; b++) {
                    int32_t q2 = q, j2 = j;
                    int64_t e;
                    step(&q2, &j2, b);
                    e = e1 + (int64_t)(x[k + 1] - q2) * (x[k + 1] - q2);
                    if (be < 0 || e < be) { be = e; c = a; }
                }
            }
        }
        step(&p, &i, c);
        if ((k % blk) % 2 == 0) out[o] = (uint8_t)c; else out[o++] |= (uint8_t)(c << 4);
    }
    if ((n % blk) % 2) o++;
}

static void decode(const uint8_t *in, uint32_t n, uint32_t blk, int16_t *y)
{
    uint32_t k, o = 0;
    int32_t p = 0, i = 0;
    for (k = 0; k < n; k++) {
        uint32_t c;
        if (k % blk == 0) {
            p = (int16_t)(in[o] | in[o + 1] << 8); i = in[o + 2]; o += 4;
        }
        c = (k % blk) % 2 == 0 ? in[o] & 15u : in[o++] >> 4;
        step(&p, &i, c);
        y[k] = (int16_t)p;
    }
}

static void *slurp(const char *f, size_t *n)
{
    FILE *h = fopen(f, "rb");
    void *b;
    if (!h) { perror(f); exit(1); }
    fseek(h, 0, SEEK_END); *n = (size_t)ftell(h); fseek(h, 0, SEEK_SET);
    b = malloc(*n + 1);
    if (fread(b, 1, *n, h) != *n) exit(1);
    fclose(h);
    return b;
}

int main(int argc, char **argv)
{
    uint32_t blk = argc > 2 ? (uint32_t)atoi(argv[2]) : 256;
    if (argc >= 5 && (!strcmp(argv[1], "enc") || !strcmp(argv[1], "look"))) {
        size_t nb;
        int16_t *x = slurp(argv[3], &nb);
        uint32_t n = (uint32_t)(nb / 2), m = bytes_of(n, blk);
        uint8_t *o = calloc(m, 1);
        FILE *h = fopen(argv[4], "wb");
        encode(x, n, blk, o, argv[1][0] == 'l');
        fwrite(o, 1, m, h); fclose(h);
        printf("%u samples -> %u bytes (%.3f bits a sample)\n", n, m, m * 8.0 / n);
        return 0;
    }
    if (argc >= 6 && !strcmp(argv[1], "dec5")) {
        size_t nb;
        uint8_t *in = slurp(argv[3], &nb);
        uint32_t n = (uint32_t)atoi(argv[4]);
        int16_t *y = malloc(n * 2);
        FILE *h = fopen(argv[5], "wb");
        decode5(in, n, blk, y);
        fwrite(y, 2, n, h); fclose(h);
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[1], "enc5")) {
        size_t nb;
        int16_t *x = slurp(argv[3], &nb);
        uint32_t n = (uint32_t)(nb / 2), m = bytes5(n, blk);
        uint8_t *o = calloc(m, 1);
        FILE *h = fopen(argv[4], "wb");
        encode5(x, n, blk, o);
        fwrite(o, 1, m, h); fclose(h);
        return 0;
    }
    if (argc >= 6 && !strcmp(argv[1], "dec")) {
        size_t nb;
        uint8_t *in = slurp(argv[3], &nb);
        uint32_t n = (uint32_t)atoi(argv[4]);
        int16_t *y = malloc(n * 2);
        FILE *h = fopen(argv[5], "wb");
        decode(in, n, blk, y);
        fwrite(y, 2, n, h); fclose(h);
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[1], "bench")) {    /* decode vs 12-bit unpack, ns a sample on this PC */
        uint32_t n = (uint32_t)atoi(argv[3]), k, r;
        int16_t *x = malloc(n * 2), *y = malloc(n * 2);
        uint8_t *a = calloc(bytes_of(n, blk), 1), *b = calloc(n / 2 * 3 + 3, 1);
        volatile int32_t sink = 0;
        struct timespec t0, t1;
        double ta, tb;
        for (k = 0; k < n; k++) x[k] = (int16_t)((k * 2654435761u) >> 20);
        encode(x, n, blk, a, 0);
        clock_gettime(CLOCK_MONOTONIC, &t0);
        for (r = 0; r < 20; r++) { decode(a, n, blk, y); sink += y[r]; }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        ta = ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / 20.0 / n;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        for (r = 0; r < 20; r++)
            for (k = 0; k < n; k++) {                    /* sp_sample: 2 in 3 bytes */
                const uint8_t *q = b + (k >> 1) * 3;
                int32_t v = (k & 1) ? (q[1] >> 4) | (q[2] << 4) : q[0] | ((q[1] & 15) << 8);
                y[k] = (int16_t)(((v ^ 0x800) - 0x800) << 4);
            }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        tb = ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / 20.0 / n;
        printf("decode %.2f ns a sample, 12-bit unpack %.2f ns: %.1fx (sink %d)\n", ta, tb, ta / tb, sink);
        return 0;
    }
    fprintf(stderr, "usage: see the top of adpcm.c\n");
    return 1;
}
