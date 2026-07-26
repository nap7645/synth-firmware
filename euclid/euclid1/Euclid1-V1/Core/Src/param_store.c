/**
  ******************************************************************************
  * @file    param_store.c
  * @brief   Persist and recall sequencer parameters in internal flash.
  ******************************************************************************
  */

#include "param_store.h"
#include <string.h>

/* ---------------------------------------------------------------------------
   FLASH GEOMETRY -- VERIFY BEFORE FIRST SAVE.

   STM32G474RE is 512 KB. In the default dual-bank configuration (option bit
   DBANK = 1) pages are 2 KB and there are 128 pages per bank, so the last page
   is bank 2 page 127 at 0x0807F800.

   If DBANK has been cleared, pages are 4 KB, there is a single bank of 128
   pages, and the last page is page 127 at 0x0807F000. Erasing the wrong page
   will not brick the part but will silently destroy whatever is there --
   confirm against RM0440 and your option bytes before trusting this.

   Check the setting in STM32CubeProgrammer under Option Bytes -> User
   Configuration -> DBANK, or read FLASH_OPTR bit 22 at runtime.
   --------------------------------------------------------------------------- */
#define PARAM_FLASH_BANK        FLASH_BANK_2
#define PARAM_FLASH_PAGE        127U
#define PARAM_FLASH_ADDR        0x0807F800UL

#define PARAM_MAGIC             0x45554331UL    /* 'EUC1' */
#define PARAM_VERSION           1U

/* Packed record. Size must stay a multiple of 8: the G4 flash programs a
   64-bit doubleword at a time and the address must be doubleword aligned. */
typedef struct
{
    uint32_t magic;
    uint16_t version;
    uint16_t tempo_bpm;

    uint8_t  n;
    uint8_t  gate_pct;
    uint8_t  k[SEQ_NUM_CHANNELS];
    uint8_t  offset[SEQ_NUM_CHANNELS];

    uint8_t  reserved;
    uint32_t checksum;
} param_record_t;

_Static_assert(sizeof(param_record_t) % 8U == 0U,
               "param_record_t must be a multiple of 8 bytes for 64-bit flash programming");

#define PARAM_RECORD_DWORDS     (sizeof(param_record_t) / 8U)

/* -------------------------------------------------------------------------- */

static uint32_t checksum_of(const param_record_t *r)
{
    /* Everything except the trailing checksum field itself. */
    const uint8_t *p = (const uint8_t *)r;
    size_t         n = sizeof(param_record_t) - sizeof(r->checksum);

    uint32_t sum = 0x811C9DC5UL;                /* FNV-1a offset basis */
    for (size_t i = 0; i < n; i++)
    {
        sum ^= p[i];
        sum *= 0x01000193UL;
    }
    return sum;
}

param_result_t param_store_load(seq_state_t *s)
{
    param_record_t r;
    memcpy(&r, (const void *)PARAM_FLASH_ADDR, sizeof(r));

    if (r.magic != PARAM_MAGIC)         return PARAM_ERR_EMPTY;
    if (r.version != PARAM_VERSION)     return PARAM_ERR_EMPTY;
    if (r.checksum != checksum_of(&r))  return PARAM_ERR_EMPTY;

    /* Range-check everything even though it checksummed -- a valid record from
       an older build could still hold an out-of-range value. */
    if (r.tempo_bpm < SEQ_TEMPO_MIN || r.tempo_bpm > SEQ_TEMPO_MAX) return PARAM_ERR_EMPTY;
    if (r.n < SEQ_N_MIN || r.n > SEQ_N_MAX)                         return PARAM_ERR_EMPTY;
    if (r.gate_pct < SEQ_GATE_MIN_PCT || r.gate_pct > SEQ_GATE_MAX_PCT) return PARAM_ERR_EMPTY;

    s->tempo_bpm = r.tempo_bpm;
    s->n         = r.n;
    s->gate_pct  = r.gate_pct;

    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
        s->k[c]      = s->k_intent[c]      = r.k[c];
        s->offset[c] = s->offset_intent[c] = r.offset[c];
    }
    return PARAM_OK;
}

param_result_t param_store_save(const seq_state_t *s)
{
    /* A page erase takes tens of milliseconds with the flash controller
       stalling the bus. Doing that under a running step ISR would be audible. */
    if (s->running) return PARAM_ERR_RUNNING;

    param_record_t r;
    memset(&r, 0, sizeof(r));

    r.magic     = PARAM_MAGIC;
    r.version   = PARAM_VERSION;
    r.tempo_bpm = s->tempo_bpm;
    r.n         = s->n;
    r.gate_pct  = s->gate_pct;

    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
        /* Save the intent values, not the clamped ones, so a saved pattern
           reloads exactly as dialled in. */
        r.k[c]      = s->k_intent[c];
        r.offset[c] = s->offset_intent[c];
    }
    r.checksum = checksum_of(&r);

    param_result_t res = PARAM_OK;

    if (HAL_FLASH_Unlock() != HAL_OK) return PARAM_ERR_FLASH;

    FLASH_EraseInitTypeDef er = {
        .TypeErase = FLASH_TYPEERASE_PAGES,
        .Banks     = PARAM_FLASH_BANK,
        .Page      = PARAM_FLASH_PAGE,
        .NbPages   = 1U,
    };

    uint32_t page_error = 0U;
    if (HAL_FLASHEx_Erase(&er, &page_error) != HAL_OK)
    {
        res = PARAM_ERR_FLASH;
    }
    else
    {
        uint64_t dw[PARAM_RECORD_DWORDS];
        memcpy(dw, &r, sizeof(r));

        for (uint32_t i = 0; i < PARAM_RECORD_DWORDS; i++)
        {
            if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD,
                                  PARAM_FLASH_ADDR + i * 8UL,
                                  dw[i]) != HAL_OK)
            {
                res = PARAM_ERR_FLASH;
                break;
            }
        }
    }

    HAL_FLASH_Lock();

    /* Read back and verify -- a silent save failure is the worst outcome here. */
    if (res == PARAM_OK)
    {
        param_record_t chk;
        memcpy(&chk, (const void *)PARAM_FLASH_ADDR, sizeof(chk));
        if (memcmp(&chk, &r, sizeof(r)) != 0) res = PARAM_ERR_FLASH;
    }

    return res;
}
