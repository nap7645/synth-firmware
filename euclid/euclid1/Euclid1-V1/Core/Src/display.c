/**
  ******************************************************************************
  * @file    display.c
  * @brief   SSD1306 OLED front panel display -- STUB.
  *
  * What is real here: the timing, the change detection, the page dispatch, and
  * the porting layer boundary. What is stubbed: every draw call.
  *
  * Everything except the public entry points sits inside EUCLID_DISPLAY, so at
  * 0 this compiles to almost nothing and pulls in no dependencies.
  *
  * To bring it up:
  *   1. Add afiskon/stm32-ssd1306 (or equivalent) to Core/Src and Core/Inc.
  *   2. Point its config header at SPI2 and the pins in the PORTING section.
  *   3. Set EUCLID_DISPLAY to 1 in euclid_config.h.
  *   4. Fill in the oled_* porting functions -- about ten lines each.
  *   5. Fill in draw_overview(). The others can wait.
  ******************************************************************************
  */

#include "display.h"
#include "euclid_config.h"
#include "seq_engine.h"
#include "ui.h"

/* Pins, per the CubeMX assignment. */
#define OLED_CS_PORT     GPIOB
#define OLED_CS_PIN      GPIO_PIN_10
#define OLED_RST_PORT    GPIOB
#define OLED_RST_PIN     GPIO_PIN_11
#define OLED_DC_PORT     GPIOB
#define OLED_DC_PIN      GPIO_PIN_12

#define OLED_WIDTH       128
#define OLED_HEIGHT      64

static display_page_t page = PAGE_OVERVIEW;

#if EUCLID_DISPLAY

static uint8_t  forced_dirty = 1;
static uint32_t last_draw_ms;

/* -------------------------------------------------------------------------- */
/* change detection                                                           */
/* -------------------------------------------------------------------------- */

/* Snapshot of everything currently on screen. Comparing against this each tick
   means seq_engine and ui need no knowledge that a display exists -- no dirty
   flags to plumb through, nothing to keep in sync. Costs one byte compare of a
   ~14 byte struct. Add a field here and change detection follows automatically. */
typedef struct
{
    uint16_t tempo_bpm;
    uint8_t  n;
    uint8_t  gate_pct;
    uint8_t  k[SEQ_NUM_CHANNELS];
    uint8_t  offset[SEQ_NUM_CHANNELS];
    uint8_t  running;
    uint8_t  shift;
    uint8_t  page;
    uint8_t  step;              /* only compared when the page wants a playhead */
} snapshot_t;

static snapshot_t shown;

/**
  * @brief  Does the current page animate with the sequencer position?
  *
  * Including a playhead means redrawing at step rate -- 20 Hz at 300 BPM with
  * 16th-note steps. Affordable, but it turns an idle display into a continuous
  * ~1 ms SPI transfer every 50 ms, so only pages that need it opt in.
  */
static uint8_t page_tracks_step(display_page_t p)
{
    return (p == PAGE_PATTERN) ? 1U : 0U;
}

static void snapshot_take(snapshot_t *s)
{
    s->tempo_bpm = seq.tempo_bpm;
    s->n         = seq.n;
    s->gate_pct  = seq.gate_pct;
    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
        s->k[c]      = seq.k[c];
        s->offset[c] = seq.offset[c];
    }
    s->running = seq.running;
    s->shift   = ui_shift_held();
    s->page    = (uint8_t)page;
    s->step    = page_tracks_step(page) ? seq.step : 0U;
}

static uint8_t snapshot_differs(const snapshot_t *a, const snapshot_t *b)
{
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    for (uint32_t i = 0; i < sizeof(snapshot_t); i++)
        if (pa[i] != pb[i]) return 1U;
    return 0U;
}

/* -------------------------------------------------------------------------- */
/* PORTING LAYER -- all stubs                                                 */
/* -------------------------------------------------------------------------- */
/* Replace the bodies with calls into whichever SSD1306 driver you add. Keeping
   them behind this boundary means the draw functions below never reference the
   driver directly, so swapping drivers or moving to I2C touches only this
   section.                                                                   */

static void oled_hw_reset(void)
{
    /* CS idles high; hold RST low briefly, then release and let the panel settle. */
    HAL_GPIO_WritePin(OLED_CS_PORT,  OLED_CS_PIN,  GPIO_PIN_SET);
    HAL_GPIO_WritePin(OLED_RST_PORT, OLED_RST_PIN, GPIO_PIN_RESET);
    HAL_Delay(10);
    HAL_GPIO_WritePin(OLED_RST_PORT, OLED_RST_PIN, GPIO_PIN_SET);
    HAL_Delay(10);
}

static void oled_driver_init(void) { /* TODO: ssd1306_Init(); */ }
static void oled_clear(void)       { /* TODO: ssd1306_Fill(Black); */ }
static void oled_flush(void)       { /* TODO: ssd1306_UpdateScreen(); */ }

/* TODO: ssd1306_SetCursor(x, y); ssd1306_WriteString(s, font, White); */
static void oled_text(uint8_t x, uint8_t y, const char *s)
{ (void)x; (void)y; (void)s; }

/* TODO: ssd1306_DrawPixel(x, y, on ? White : Black); */
static void oled_pixel(uint8_t x, uint8_t y, uint8_t on)
{ (void)x; (void)y; (void)on; }

/* TODO: ssd1306_DrawRectangle / ssd1306_FillRectangle */
static void oled_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t filled)
{ (void)x; (void)y; (void)w; (void)h; (void)filled; }

/* -------------------------------------------------------------------------- */
/* DRAW FUNCTIONS -- all stubs, this is the design work                       */
/* -------------------------------------------------------------------------- */

/**
  * @brief  Default page. Everything you need at a glance while playing.
  *
  * Suggested layout on 128x64, roughly four 16px rows:
  *
  *     +----------------------------------+
  *     | 128 BPM      N=16      GATE 50%  |   <- globals
  *     | K1  4  o0   ####.###.####.###.   |   <- per channel: k, offset, and a
  *     | K2  5  o2   #..#..#..#..#...     |      compressed pattern strip
  *     | K3  7  o0   #.#.#.#.#.#.#.#.     |
  *     +----------------------------------+
  *
  * The run/stop indicator has to go somewhere -- inverting the top row while
  * stopped reads well and costs no space.
  */
static void draw_overview(void)
{
    /* TODO: the actual design. Reads seq.tempo_bpm, seq.n, seq.gate_pct,
       seq.k[], seq.offset[], seq.pat[], seq.running. */
    oled_text(0, 0, "EUCLID 1");
    (void)oled_pixel; (void)oled_rect;
}

/**
  * @brief  Pattern-focused page with a moving playhead.
  *
  * Two shapes worth considering. A ring per channel is the most legible for
  * cyclic rhythm -- 64 steps around a circle, hits filled, playhead brighter --
  * but three rings on 128x64 leaves each about 20 px across, which is tight.
  * A grid is less elegant and much more readable: three rows of up to 64 cells
  * at two pixels per cell.
  *
  * This is the page that needs redrawing at step rate; see page_tracks_step().
  */
static void draw_pattern(void)
{
    /* TODO. Reads seq.pat[], seq.pat_sum, seq.n, seq.step. */
    oled_text(0, 0, "PATTERN");
}

/**
  * @brief  Shown while Shift is held: what each encoder currently does.
  *
  * This is the page that earns its keep during a long set -- five encoders with
  * two functions each is exactly what you forget at hour three.
  *
  *     TEMPO -> GATE LENGTH        N -> LENGTH x2
  *     K1 -> OFFSET 1   K2 -> OFFSET 2   K3 -> OFFSET 3
  *     hold both 2s -> SAVE        tap both -> PAUSE
  */
static void draw_shift(void)
{
    /* TODO. Static text, plus live values for whatever Shift is modifying. */
    oled_text(0, 0, "SHIFT");
}

#endif /* EUCLID_DISPLAY */

/* -------------------------------------------------------------------------- */
/* public                                                                     */
/* -------------------------------------------------------------------------- */

void display_init(void)
{
#if EUCLID_DISPLAY
    oled_hw_reset();
    oled_driver_init();
    oled_clear();
    oled_flush();

    forced_dirty = 1U;
    last_draw_ms = HAL_GetTick();

    /* Deliberately not snapshot_take() here -- leaving the snapshot zeroed
       guarantees the first tick draws something. */
#endif
    page = PAGE_OVERVIEW;
}

void display_mark_dirty(void)
{
#if EUCLID_DISPLAY
    forced_dirty = 1U;
#endif
}

void display_set_page(display_page_t p)
{
    if (p >= PAGE_COUNT || p == page) return;
    page = p;
#if EUCLID_DISPLAY
    forced_dirty = 1U;
#endif
}

display_page_t display_get_page(void)
{
    return page;
}

void display_tick(void)
{
#if EUCLID_DISPLAY
    uint32_t now = HAL_GetTick();
    if ((now - last_draw_ms) < DISPLAY_MIN_INTERVAL_MS) return;

    snapshot_t current;
    snapshot_take(&current);

    if (!forced_dirty && !snapshot_differs(&current, &shown)) return;

    last_draw_ms = now;
    forced_dirty = 0U;
    shown        = current;

    oled_clear();

    /* Shift takes over the screen while held -- it is the most useful thing to
       see at that moment, and it needs no navigation. */
    if (current.shift)
    {
        draw_shift();
    }
    else
    {
        switch (page)
        {
            case PAGE_PATTERN:  draw_pattern();  break;
            case PAGE_SHIFT:    draw_shift();    break;
            case PAGE_OVERVIEW:
            default:            draw_overview(); break;
        }
    }

    oled_flush();
#endif
}
