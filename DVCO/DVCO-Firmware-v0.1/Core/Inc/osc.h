/**
 * osc.h — oscillator core. Portable, fixed-point, no HAL dependency.
 *
 * This file and osc.c are the part that must run unchanged on the STM32C031
 * (Cortex-M0+, no FPU). Keep it free of floats, of 64-bit math in the audio
 * path, and of any STM32-family header. Anything hardware-specific belongs
 * in main.c, not here.
 *
 * Method: 32-bit phase accumulator. Each sample, phase += phase_inc, where
 *   phase_inc = freq * 2^32 / sample_rate
 * so the accumulator wraps exactly once per waveform cycle. The top bits of
 * phase index a wavetable (sine) or are shaped arithmetically (saw/sq/tri).
 */
#ifndef __OSC_H
#define __OSC_H

#include <stdint.h>

typedef struct {
  uint32_t phase;       /* current phase, full 32-bit range = one cycle   */
  uint32_t phase_inc;   /* per-sample phase increment (sets frequency)    */
  uint32_t sample_rate; /* Hz                                            */
  uint16_t out_max;     /* output scaled to 0..out_max (the PWM ARR)      */
  uint8_t  wave;        /* wave_t                                        */
  uint8_t  amp_pct;     /* 0..100, amplitude about the midpoint          */
  uint8_t  wrapped;     /* set for one sample when phase wrapped a cycle  */
} osc_t;

void     osc_init(osc_t *o, uint32_t sample_rate, uint16_t out_max);
void     osc_set_freq(osc_t *o, uint32_t freq_hz);
uint32_t osc_get_freq(const osc_t *o);
void     osc_set_wave(osc_t *o, uint8_t wave);
void     osc_set_amp(osc_t *o, uint8_t amp_pct);

/* Advance one sample and return the value to write to the PWM compare
 * register. Called from the audio ISR — keep it cheap. */
uint16_t osc_next(osc_t *o);

#endif /* __OSC_H */
