/*
 * itcmtest: a no-op test of ITCM hooks (Polyend Tracker+ firmware 1.2.0).
 *
 * The sequencer's time-critical code runs from ITCM, copied there from flash at boot.
 * ITCM code can't BL to patch code in flash, so tools/patch_ptf.py ("itcm_branches")
 * points an ITCM BL at a small stub that it places at the start of the ITCM tail and
 * loads through the startup copy table's unused entry 3; the stub jumps to the hook.
 * This patch hooks two ITCM call sites and changes nothing they do:
 *  - ITCM 0x30ce, "bl rand_range_q" in the Chance FX pre-pass of seq_process_track
 *    (runs at a step with a Chance FX). The stub passes r7 (the FX column's record
 *    base: seq + 0x68 * track, minus 2 for FX1) and r8 (the track) as arguments 3 and 4;
 *    mt_it_chance counts the call, keeps them, and calls rand_range_q as stock does.
 *  - ITCM 0x3752, "bl seq_play_from" at the end of a count-in in seq_tick (no
 *    arguments passed); mt_it_play_from counts the call and starts playback as stock.
 * The counts and arguments (this patch's RAM) let the emulator test check that the
 * stubs run and pass the right registers; the firmware plays exactly as stock.
 *
 * Hooks go through chain words (mt_it_next_*) that tools/patch_ptf.py fills with what
 * the site called before: the stock function or another patch's hook.
 */
#include <stdint.h>

#ifndef ITCMTEST_VERSION
#define ITCMTEST_VERSION "it1"
#endif

typedef int32_t (*rand_fn)(int32_t lo, int32_t hi, const uint8_t *column, uint32_t track);
typedef void (*play_from_fn)(void *seq, uint32_t step);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_it_next_chance);              /* rand_range_q, ITCM 0x3af5 */
CHAIN_WORD(mt_it_next_play_from);           /* seq_play_from, ITCM 0x1251 */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define IT_MAGIC            0x54494D47u     /* "GMIT" */

struct it_state {
    uint32_t magic;
    uint32_t chance_calls;
    uint32_t play_from_calls;
    uint32_t last_column;                   /* r7 at the last Chance call */
    uint32_t last_track;                    /* r8 at the last Chance call */
};
static struct it_state it_state_ram __attribute__((section(".mt_ram")));
#define IT (&it_state_ram)

static void it_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (IT->magic == IT_MAGIC)
        return;
    IT->chance_calls = 0;
    IT->play_from_calls = 0;
    IT->last_column = 0;
    IT->last_track = 0;
    IT->magic = IT_MAGIC;
}

/* ITCM 0x30ce: "bl rand_range_q(0, fx_max)" -> stub (r2 = r7, r3 = r8) -> here */
int32_t mt_it_chance(int32_t lo, int32_t hi, const uint8_t *column, uint32_t track)
{
    it_init_if_needed();
    IT->chance_calls++;
    IT->last_column = (uint32_t)column;
    IT->last_track = track;
    return ((rand_fn)mt_it_next_chance)(lo, hi, column, track);
}

/* ITCM 0x3752: "bl seq_play_from(seq, 0)" at the end of a count-in -> stub -> here */
void mt_it_play_from(void *seq, uint32_t step)
{
    it_init_if_needed();
    IT->play_from_calls++;
    ((play_from_fn)mt_it_next_play_from)(seq, step);
}

/* ---- version text: Config shows 1.2.0-<ITCMTEST_VERSION> ----------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" ITCMTEST_VERSION;
