/**
  ******************************************************************************
  * @file    euclid_gen.h
  * @brief   Euclidean pattern generation and rotation.
  *
  * Pure functions, no hardware dependency, no state. This file compiles with
  * any C99 compiler, so you can unit-test it on your laptop:
  *
  *     cc -DEUCLID_GEN_TEST euclid_gen.c -o t && ./t
  ******************************************************************************
  */
#ifndef EUCLID_GEN_H
#define EUCLID_GEN_H

#include <stdint.h>

/**
  * @brief  Bit mask of the low n bits.
  * @param  n  0..64
  */
uint64_t euclid_mask(uint8_t n);

/**
  * @brief  Generate a Euclidean rhythm of k hits distributed over n steps.
  * @param  n  pattern length, 1..64
  * @param  k  number of hits, 0..n
  * @retval Pattern word. Step 0 is the LSB (bit 0), step n-1 is bit n-1.
  *
  * Bit 0 is always set when k > 0, so every pattern lands on the downbeat and
  * stays in phase with SOC.
  */
uint64_t euclid_generate(uint8_t n, uint8_t k);

/**
  * @brief  Rotate a pattern later in time (clockwise on the offset encoder).
  * @param  pattern  pattern word from euclid_generate()
  * @param  n        pattern length, must match what generated it
  * @param  offset   0..n-1, taken mod n
  * @retval Rotated pattern, still masked to n bits.
  *
  * Increasing offset delays the pattern: a hit on step 0 with offset 1 moves to
  * step 1, and hits falling off the end wrap around to the start.
  */
uint64_t euclid_rotate(uint64_t pattern, uint8_t n, uint8_t offset);

/**
  * @brief  Population count, for asserting that a generated pattern really does
  *         contain k hits. Cheap enough to leave in.
  */
uint8_t euclid_popcount(uint64_t pattern);

#endif /* EUCLID_GEN_H */
