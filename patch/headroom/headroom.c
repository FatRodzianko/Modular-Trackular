/*
 * headroom: how much of each audio block's time the firmware uses (Polyend Tracker+
 * firmware 1.2.0). A diagnostic, not a feature: docs/TIMING_HEADROOM_PLAN.md.
 *
 * Every audio block runs app_audio_block (0x600aa7c8) inside the audio DMA interrupt,
 * once per 128 frames (about 2,901 us). This patch reads the Cortex-M7 cycle counter
 * (DWT CYCCNT), which stock firmware already runs from boot (timer_init_q 0x600a24a4,
 * FINDINGS section 31), at three places:
 *  - around the whole block: the app vtable's +8 word (0x60038724) -> mt_hr_block;
 *  - around the sequencer: "bl seq_advance" at 0x600aa7f8 -> mt_hr_seq_advance. List
 *    headroom last in a bundle, so this hook runs first and its time includes every
 *    other patch's hook on that site;
 *  - block start to block start: the period. Percentages are of the measured period,
 *    so no clock rate is assumed. A period over 112.5 % of the smoothed one is a late
 *    block (the block before it overran, or something held the interrupt off).
 * The figures reset each time playback starts. While an export runs (0x20026e44 set),
 * app_audio_block returns at once and the export renders from the main loop, so
 * nothing is counted then.
 *
 * Config shows the figures, once a second, in place of stock's two CPU lines on the
 * developer screen (an empty file named .develop in the SD card's root):
 *   blk 61.3% seq 4.1%     worst block, worst sequencer share, since playback started
 *   1s 40.2% late 0        worst block of the last second, late blocks since then
 * The two "bl snprintf" in cfg_debug_info (0x600cb570, 0x600cb590) are hooked for it.
 *
 * The patch only reads debug registers, never writes them. Shared hooks go through
 * chain words (mt_hr_next_*) that tools/patch_ptf.py fills with what the site pointed
 * to before.
 */
#include <stdint.h>

#ifndef HEADROOM_VERSION
#define HEADROOM_VERSION "hr1"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define DWT_CTRL            (*(volatile const uint32_t *)0xE0001000u)
#define DWT_CYCCNT          (*(volatile const uint32_t *)0xE0001004u)
#define DWT_CYCCNTENA       1u
#define EXPORTING           (*(volatile const uint8_t *)0x20026E44u)   /* app_audio_block returns at once */
#define SEQ_PLAYING         (*(volatile const uint8_t *)(0x20017274u + 0x2Au))

typedef void (*block_fn)(void *app, void *a1, void *a2, uint32_t frames);
typedef void (*advance_fn)(void *seq, float seconds);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_hr_next_block);               /* app_audio_block 0x600aa7c9 */
CHAIN_WORD(mt_hr_next_seq_advance);         /* seq_advance (veneer 0x600aa859) */
CHAIN_WORD(mt_hr_next_line1);               /* snprintf 0x6016712d */
CHAIN_WORD(mt_hr_next_line2);               /* snprintf 0x6016712d */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define HR_MAGIC            0x5248544Du     /* "MTHR" */
#define HR_WINDOW           345u            /* blocks in about one second */
#define HR_NOMINAL_SHIFT    6               /* the smoothed period follows 1/64 of each change */
#define HR_LATE_SHIFT       3               /* late: over nominal + nominal / 8 */

struct hr_state {
    uint32_t magic;
    uint32_t t_prev;                        /* CYCCNT at the previous block's start */
    uint32_t nominal;                       /* smoothed period, cycles; 0: none yet */
    uint32_t blk_max;                       /* worst block since the reset, cycles */
    uint32_t seq_max;                       /* worst sequencer share since the reset */
    uint32_t win_max;                       /* worst block of the current window */
    uint32_t last_win;                      /* ... of the last full window */
    uint32_t win_n;                         /* blocks in the current window */
    uint32_t late;                          /* late blocks since the reset */
    uint32_t seq_cur;                       /* this block's sequencer time so far */
    uint8_t have_prev;                      /* t_prev is valid */
    uint8_t was_playing;
};
static struct hr_state hr_state_ram __attribute__((section(".mt_ram")));
#define HR (&hr_state_ram)

static void hr_reset(void)                  /* the figures; the period stays */
{
    HR->blk_max = 0;
    HR->seq_max = 0;
    HR->win_max = 0;
    HR->last_win = 0;
    HR->win_n = 0;
    HR->late = 0;
}

static void hr_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (HR->magic == HR_MAGIC)
        return;
    hr_reset();
    HR->nominal = 0;
    HR->have_prev = 0;
    HR->was_playing = 0;
    HR->seq_cur = 0;
    HR->magic = HR_MAGIC;
}

/* ---- the audio interrupt --------------------------------------------------------- */

/* app vtable +8 (0x60038724): app_audio_block(app, out, in, frames), chained */
void mt_hr_block(void *app, void *a1, void *a2, uint32_t frames)
{
    uint32_t t0 = DWT_CYCCNT;
    hr_init_if_needed();
    if (EXPORTING) {                        /* no block work; restart the period after */
        HR->have_prev = 0;
        ((block_fn)mt_hr_next_block)(app, a1, a2, frames);
        return;
    }
    uint8_t playing = SEQ_PLAYING;
    if (playing && !HR->was_playing)
        hr_reset();
    HR->was_playing = playing;
    if (HR->have_prev) {
        uint32_t p = t0 - HR->t_prev, nom = HR->nominal;
        if (nom == 0) {
            HR->nominal = p;
        } else {
            if (p > nom + (nom >> HR_LATE_SHIFT))
                HR->late++;
            HR->nominal = nom + (uint32_t)((int32_t)(p - nom) >> HR_NOMINAL_SHIFT);
        }
    }
    HR->t_prev = t0;
    HR->have_prev = 1;
    HR->seq_cur = 0;

    ((block_fn)mt_hr_next_block)(app, a1, a2, frames);

    uint32_t w = DWT_CYCCNT - t0;
    if (w > HR->blk_max)
        HR->blk_max = w;
    if (HR->seq_cur > HR->seq_max)
        HR->seq_max = HR->seq_cur;
    if (w > HR->win_max)
        HR->win_max = w;
    if (++HR->win_n >= HR_WINDOW) {
        HR->last_win = HR->win_max;
        HR->win_max = 0;
        HR->win_n = 0;
    }
}

/* replaces "bl seq_advance" (veneer 0x600aa858) at 0x600aa7f8 in app_audio_block (chained) */
void mt_hr_seq_advance(void *seq, float seconds)
{
    uint32_t s0 = DWT_CYCCNT;
    ((advance_fn)mt_hr_next_seq_advance)(seq, seconds);
    HR->seq_cur += DWT_CYCCNT - s0;
}

/* ---- Config's developer lines (main loop, once a second) ------------------------- */

#define HR_PCT10_MAX        9999u           /* 999.9 % */
#define HR_LATE_MAX         99999u          /* keeps line 2 within 21 characters */

/* cycles as tenths of a percent of the period (nominal / 1000: off by under 0.1 % for a
 * period of about 1.5 million cycles, and no 64-bit division) */
static uint32_t hr_pct10(uint32_t cycles, uint32_t nominal)
{
    uint32_t per = nominal / 1000u;
    if (per == 0)
        return HR_PCT10_MAX;
    uint32_t v = cycles / per;
    return v > HR_PCT10_MAX ? HR_PCT10_MAX : v;
}

static int hr_ready(void)
{
    return HR->magic == HR_MAGIC && HR->nominal != 0 && (DWT_CTRL & DWT_CYCCNTENA);
}

/* cfg_debug_info 0x600cb570: snprintf(line, 30, "min: %d%%, max: %d%%", min, max) */
int mt_hr_line1(char *buf, uint32_t size, const char *fmt, int a, int b)
{
    (void)fmt; (void)a; (void)b;
    snprintf_fn out = (snprintf_fn)mt_hr_next_line1;
    if (!(DWT_CTRL & DWT_CYCCNTENA))
        return out(buf, size, "no cycle counter");
    if (!hr_ready())
        return out(buf, size, "headroom: waiting");
    uint32_t blk = hr_pct10(HR->blk_max, HR->nominal), seq = hr_pct10(HR->seq_max, HR->nominal);
    return out(buf, size, "blk %u.%u%% seq %u.%u%%", blk / 10, blk % 10, seq / 10, seq % 10);
}

/* cfg_debug_info 0x600cb590: snprintf(line, 30, "avg: %d%%, last: %d%%", avg, last) */
int mt_hr_line2(char *buf, uint32_t size, const char *fmt, int a, int b)
{
    (void)fmt; (void)a; (void)b;
    snprintf_fn out = (snprintf_fn)mt_hr_next_line2;
    if (!hr_ready())
        return out(buf, size, "");
    uint32_t win = HR->last_win > HR->win_max ? HR->last_win : HR->win_max;
    uint32_t pct = hr_pct10(win, HR->nominal), late = HR->late;
    return out(buf, size, "1s %u.%u%% late %u", pct / 10, pct % 10, late > HR_LATE_MAX ? HR_LATE_MAX : late);
}

/* ---- version text: Config shows 1.2.0-<HEADROOM_VERSION> -------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" HEADROOM_VERSION;
