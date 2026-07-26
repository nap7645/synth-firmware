/**
  ******************************************************************************
  * @file    display.h
  * @brief   SSD1306 OLED front panel display (Adafruit 326, 128x64, SPI2).
  *
  * STUB. The structure, timing and change-detection are real and working; every
  * function that actually puts pixels on glass is a no-op marked TODO.
  *
  * Compiles and links with no external dependencies while EUCLID_DISPLAY is 0,
  * so it can live in the tree before the SSD1306 driver is added.
  *
  * Hardware (see EUCLID1_CUBEMX_CONFIG.md section 8):
  *   SPI2   PB13 SCK, PB15 MOSI, PB14 MISO (unused)
  *   PB10   OLED_CS   active low, idles high
  *   PB11   OLED_RST  active low, starts low (panel held in reset)
  *   PB12   OLED_DC   low = command, high = data
  ******************************************************************************
  */
#ifndef DISPLAY_H
#define DISPLAY_H

#include "main.h"
#include <stdint.h>

/**
  * @brief  Release the panel from reset and run the SSD1306 init sequence.
  *         Safe to call when EUCLID_DISPLAY is 0 -- does nothing.
  */
void display_init(void);

/**
  * @brief  Redraw if anything changed. Call from app_tick(); rate-limits itself
  *         to DISPLAY_MIN_INTERVAL_MS and skips entirely when nothing is dirty.
  *
  *         Never call from an ISR. A full 1024-byte frame at 10.6 MHz blocks for
  *         roughly a millisecond, which the step ISR preempts cleanly at
  *         priority 0 -- but only because this runs in the main loop.
  */
void display_tick(void);

/**
  * @brief  Force a redraw on the next tick. Change detection already covers all
  *         sequencer parameters, so this is only needed for things it cannot
  *         see -- a save confirmation, an error message, a page change.
  */
void display_mark_dirty(void);

/**
  * @brief  Which screen is showing. Only PAGE_OVERVIEW is drawn today; the rest
  *         are placeholders for whatever the design lands on.
  */
typedef enum
{
    PAGE_OVERVIEW = 0,      /* tempo, N, three k/offset pairs, run state */
    PAGE_PATTERN,           /* pattern rings or grid with a playhead */
    PAGE_SHIFT,             /* what each encoder does while Shift is held */
    PAGE_COUNT
} display_page_t;

void           display_set_page(display_page_t p);
display_page_t display_get_page(void);

#endif /* DISPLAY_H */
