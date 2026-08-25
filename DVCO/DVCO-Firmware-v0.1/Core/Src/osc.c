#include "osc.h"

/* Quarter-wave would save flash, but at 256 entries the full table is only
 * 512 bytes and costs no unfolding logic in the ISR. Values are a sine
 * normalised to 0..65535 with 32768 as the zero crossing. */
static const uint16_t sine_tab[256] = {
  32768, 33572, 34376, 35178, 35980, 36779, 37576, 38370,
  39161, 39947, 40730, 41507, 42280, 43046, 43807, 44561,
  45307, 46047, 46778, 47500, 48214, 48919, 49614, 50298,
  50972, 51636, 52287, 52927, 53555, 54171, 54773, 55362,
  55938, 56499, 57047, 57579, 58097, 58600, 59087, 59558,
  60013, 60451, 60873, 61278, 61666, 62036, 62389, 62724,
  63041, 63339, 63620, 63881, 64124, 64348, 64553, 64739,
  64905, 65053, 65180, 65289, 65377, 65446, 65496, 65525,
  65535, 65525, 65496, 65446, 65377, 65289, 65180, 65053,
  64905, 64739, 64553, 64348, 64124, 63881, 63620, 63339,
  63041, 62724, 62389, 62036, 61666, 61278, 60873, 60451,
  60013, 59558, 59087, 58600, 58097, 57579, 57047, 56499,
  55938, 55362, 54773, 54171, 53555, 52927, 52287, 51636,
  50972, 50298, 49614, 48919, 48214, 47500, 46778, 46047,
  45307, 44561, 43807, 43046, 42280, 41507, 40730, 39947,
  39161, 38370, 37576, 36779, 35980, 35178, 34376, 33572,
  32768, 31964, 31160, 30358, 29556, 28757, 27960, 27166,
  26375, 25589, 24806, 24029, 23256, 22490, 21729, 20975,
  20229, 19489, 18758, 18036, 17322, 16617, 15922, 15238,
  14564, 13900, 13249, 12609, 11981, 11365, 10763, 10174,
   9598,  9037,  8489,  7957,  7439,  6936,  6449,  5978,
   5523,  5085,  4663,  4258,  3870,  3500,  3147,  2812,
   2495,  2197,  1916,  1655,  1412,  1188,   983,   797,
    631,   483,   356,   247,   159,    90,    40,    11,
      1,    11,    40,    90,   159,   247,   356,   483,
    631,   797,   983,  1188,  1412,  1655,  1916,  2197,
   2495,  2812,  3147,  3500,  3870,  4258,  4663,  5085,
   5523,  5978,  6449,  6936,  7439,  7957,  8489,  9037,
   9598, 10174, 10763, 11365, 11981, 12609, 13249, 13900,
  14564, 15238, 15922, 16617, 17322, 18036, 18758, 19489,
  20229, 20975, 21729, 22490, 23256, 24029, 24806, 25589,
  26375, 27166, 27960, 28757, 29556, 30358, 31160, 31964
};

void osc_init(osc_t *o, uint32_t sample_rate, uint16_t out_max)
{
  o->phase = 0;
  o->phase_inc = 0;
  o->sample_rate = sample_rate;
  o->out_max = out_max;
  o->wave = 0;
  o->amp_pct = 100;
  o->wrapped = 0;
  osc_set_freq(o, 440);
}

/* 64-bit divide is fine here: this runs on a parameter change, never in the
 * audio ISR. On the M0+ it is a slow library call but happens at most a few
 * times a second. */
void osc_set_freq(osc_t *o, uint32_t freq_hz)
{
  if (freq_hz > o->sample_rate / 2U) {
    freq_hz = o->sample_rate / 2U;   /* clamp at Nyquist */
  }
  o->phase_inc = (uint32_t)(((uint64_t)freq_hz << 32) / o->sample_rate);
}

uint32_t osc_get_freq(const osc_t *o)
{
  return (uint32_t)(((uint64_t)o->phase_inc * o->sample_rate) >> 32);
}

void osc_set_wave(osc_t *o, uint8_t wave)
{
  o->wave = wave;
}

void osc_set_amp(osc_t *o, uint8_t amp_pct)
{
  o->amp_pct = (amp_pct > 100U) ? 100U : amp_pct;
}

uint16_t osc_next(osc_t *o)
{
  uint32_t prev = o->phase;
  o->phase += o->phase_inc;
  /* Unsigned wrap means we completed a cycle. The host-side scope uses this
   * as a trigger so the displayed waveform stands still instead of sliding. */
  o->wrapped = (o->phase < prev) ? 1U : 0U;

  /* raw: 0..65535, the waveform before amplitude/offset scaling */
  uint16_t raw;
  switch (o->wave) {
    default:
    case 0: /* SINE — table lookup on the top 8 bits */
      raw = sine_tab[o->phase >> 24];
      break;
    case 1: /* SAW — the accumulator itself is already a rising ramp */
      raw = (uint16_t)(o->phase >> 16);
      break;
    case 2: /* SQUARE — first half of the cycle high, second half low */
      raw = (o->phase & 0x80000000UL) ? 65535U : 0U;
      break;
    case 3: { /* TRIANGLE — fold the saw about its midpoint */
      uint16_t s = (uint16_t)(o->phase >> 16);
      raw = (s < 32768U) ? (uint16_t)(s << 1) : (uint16_t)((65535U - s) << 1);
      break;
    }
  }

  /* Scale 0..65535 down to 0..out_max, then apply amplitude about the
   * midpoint so reducing amplitude collapses toward 50% duty (0 V after the
   * DC-blocking cap) rather than toward 0 V. All 32-bit, no divide. */
  int32_t centred = ((int32_t)raw - 32768) * (int32_t)o->amp_pct / 100;
  int32_t scaled  = (int32_t)(((uint32_t)(centred + 32768) * (o->out_max + 1U)) >> 16);

  if (scaled < 0) scaled = 0;
  if (scaled > (int32_t)o->out_max) scaled = (int32_t)o->out_max;
  return (uint16_t)scaled;
}
