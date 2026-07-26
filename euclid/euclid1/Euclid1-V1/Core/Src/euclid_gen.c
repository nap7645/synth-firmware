/**
  ******************************************************************************
  * @file    euclid_gen.c
  * @brief   Euclidean pattern generation and rotation.
  ******************************************************************************
  */

#include "euclid_gen.h"

uint64_t euclid_mask(uint8_t n)
{
    if (n == 0)  return 0ULL;
    if (n >= 64) return ~0ULL;          /* 1ULL << 64 is undefined behaviour */
    return (1ULL << n) - 1ULL;
}

uint64_t euclid_generate(uint8_t n, uint8_t k)
{
    if (n == 0) return 0ULL;
    if (n > 64) n = 64;
    if (k == 0) return 0ULL;            /* must be before the seed below */
    if (k > n)  k = n;

    uint64_t pattern = 0ULL;

    /* Bresenham line-drawing accumulator. Seeding at (n - k) rather than 0 is
       what puts a hit on step 0.
       With cnt = 0, E(3,8) comes out as ..x..x.x (bits 2,5,7) -- three hits in
       the right places but nothing on the downbeat, which would leave every
       pattern out of phase with SOC. Seeding at n - k gives x..x..x. (bits
       0,3,6): same rhythm, rotated to start on the beat. */
    uint32_t cnt = (uint32_t)(n - k);

    for (uint8_t i = 0; i < n; i++)
    {
        cnt += k;
        if (cnt >= n)
        {
            pattern |= (1ULL << i);
            cnt -= n;
        }
    }
    return pattern;
}

uint64_t euclid_rotate(uint64_t pattern, uint8_t n, uint8_t offset)
{
    if (n == 0) return 0ULL;
    if (n > 64) n = 64;

    offset = (uint8_t)(offset % n);

    uint64_t m = euclid_mask(n);
    pattern &= m;

    if (offset == 0) return pattern;    /* pattern >> (n - 0) would be UB at n=64 */

    return ((pattern << offset) | (pattern >> (n - offset))) & m;
}

uint8_t euclid_popcount(uint64_t pattern)
{
    uint8_t c = 0;
    while (pattern)
    {
        pattern &= (pattern - 1ULL);
        c++;
    }
    return c;
}

/* -------------------------------------------------------------------------- */
/* Host-side self test. Not compiled into the firmware.                       */
/* -------------------------------------------------------------------------- */
#ifdef EUCLID_GEN_TEST
#include <stdio.h>
#include <assert.h>

static void show(uint8_t n, uint8_t k, uint8_t off)
{
    uint64_t p = euclid_rotate(euclid_generate(n, k), n, off);
    printf("E(%2u,%2u) off %2u  ", k, n, off);
    for (uint8_t i = 0; i < n; i++) putchar(((p >> i) & 1ULL) ? 'x' : '.');
    printf("   hits=%u\n", euclid_popcount(p));
}

int main(void)
{
    /* Hit count must always equal k, for every n and k in range. */
    for (uint8_t n = 1; n <= 64; n++)
        for (uint8_t k = 0; k <= n; k++)
            assert(euclid_popcount(euclid_generate(n, k)) == k);

    /* Downbeat must always be present when there is at least one hit. */
    for (uint8_t n = 1; n <= 64; n++)
        for (uint8_t k = 1; k <= n; k++)
            assert(euclid_generate(n, k) & 1ULL);

    /* Rotation preserves hit count and is cyclic with period n. */
    for (uint8_t n = 1; n <= 64; n++)
        for (uint8_t k = 0; k <= n; k++)
        {
            uint64_t base = euclid_generate(n, k);
            assert(euclid_rotate(base, n, 0) == base);
            assert(euclid_rotate(base, n, n) == base);
            for (uint8_t o = 0; o < n; o++)
                assert(euclid_popcount(euclid_rotate(base, n, o)) == k);
        }

    /* Known-good patterns from the literature. */
    show(16, 4, 0);   /* x...x...x...x...  four on the floor */
    show(16, 5, 0);   /* x...x..x..x..x..  E(5,16) */
    show(8,  3, 0);   /* x..x..x.          tresillo */
    show(8,  5, 0);   /* x.xx.xx.          cinquillo family */
    show(13, 5, 0);   /* x..x.x..x.x..     E(5,13) */
    show(8,  3, 2);   /* rotation sanity */

    printf("\nall assertions passed\n");
    return 0;
}
#endif /* EUCLID_GEN_TEST */
