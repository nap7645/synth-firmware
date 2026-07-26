/**
  ******************************************************************************
  * @file    param_store.h
  * @brief   Persist and recall sequencer parameters in internal flash.
  *
  * Saving is an explicit gesture (both buttons held 2 s) and is refused while
  * running, so a flash page erase can never stall the step ISR and jitter the
  * clock. There is no autosave.
  ******************************************************************************
  */
#ifndef PARAM_STORE_H
#define PARAM_STORE_H

#include <stdint.h>
#include "seq_engine.h"

typedef enum
{
    PARAM_OK = 0,
    PARAM_ERR_RUNNING,                  /* refused: sequencer must be stopped */
    PARAM_ERR_FLASH,                    /* erase or program failed */
    PARAM_ERR_EMPTY,                    /* nothing valid stored */
} param_result_t;

/**
  * @brief  Read stored parameters into @p s. Leaves @p s untouched and returns
  *         PARAM_ERR_EMPTY if the magic or checksum does not verify, so a
  *         virgin board or a corrupted page silently falls back to defaults.
  */
param_result_t param_store_load(seq_state_t *s);

/**
  * @brief  Erase the parameter page and write @p s to it. Blocks for the
  *         duration of the erase (tens of milliseconds) with interrupts
  *         disabled, so it refuses unless the sequencer is stopped.
  */
param_result_t param_store_save(const seq_state_t *s);

#endif /* PARAM_STORE_H */
