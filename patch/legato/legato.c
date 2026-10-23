/*
 * legato: legato for MIDI instruments (Polyend Tracker+ firmware 1.2.0).
 *
 * When a track plays a note while its previous note is still on, the sequencer
 * (seq_process_track, ITCM) first sends the old note's note-off,
 * seq_note_off_q(seq, t, last record, -4) at ITCM 0x24da, then plays the new note,
 * seq_trigger_note(seq, t, work record) at ITCM 0x2fd8, in the same call. A mono synth
 * plays legato (no envelope retrigger, legato portamento) only when the new note-on
 * arrives while the old note is still held. So for a step marked legato this patch
 * swaps the two: mt_lg_note_off keeps a copy of the old note instead of ending it, and
 * mt_lg_trigger ends it right after the new note has gone out.
 *
 * The marker is a Glide FX (G, type 19), any value, in FX1 or FX2 of a MIDI-instrument
 * note (instruments 48..63, channels 1..16). Stock firmware ignores G on MIDI notes, so
 * such patterns play there as before. The stock order stays when:
 *  - the old note isn't a MIDI instrument, or isn't on any more (its gate ended:
 *    track +0x80 is 0);
 *  - either note has Chord (14) or Arp (21);
 *  - the new note has the old one's pitch and instrument (on 60, off 60 would end the
 *    new note). This is checked at 0x2fd8, after Random note and the performance
 *    transpose have set the note.
 * A Chance FX or condtrig condition that fails skips both sites, so nothing is kept.
 *
 * The copy only lives between the two calls. Should a copy ever be left (no path that
 * does this is known), the next hook call or the end of the audio block (the patched
 * seq_advance calls, chained) ends the note, so no copy outlives an audio block.
 *
 * Shared hooks go through chain words (mt_lg_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef LEGATO_VERSION
#define LEGATO_VERSION "lg1"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define SEQ_TRACK(t)        (0x68u * (t))
#define TRK_LAST            0x68u           /* last triggered record */
#define TRK_WORK            0x6Fu           /* this step's record */
#define TRK_GATE_OPEN       0x80u           /* note on, note-off still to come */
#define TRACKS              16u
#define REC_SIZE            7u              /* note, instrument, FX1 type, value, FX2 type, value, volume */
#define REC_NOTE            0u
#define REC_INSTR           1u
#define REC_FX1             2u
#define REC_FX2             4u
#define INSTR_MIDI_FIRST    48u             /* MIDI channel 1 */
#define INSTR_MIDI_LAST     63u             /* MIDI channel 16 */
#define FX_CHORD            14u
#define FX_GLIDE            19u
#define FX_ARP              21u
#define NOTE_OFF_MODE       (-4)            /* what the sequencer passes at 0x24da */

typedef void (*note_off_fn)(void *seq, uint32_t track, const uint8_t *rec, int32_t mode);
typedef void (*trigger_fn)(void *seq, uint32_t track, const uint8_t *rec);
typedef void (*advance_fn)(void *seq, float seconds);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_lg_next_note_off);            /* seq_note_off_q, ITCM 0x11a5 */
CHAIN_WORD(mt_lg_next_trigger);             /* seq_trigger_note, ITCM 0x149d */
CHAIN_WORD(mt_lg_next_seq_advance);         /* seq_advance (veneer 0x600aa859) */
CHAIN_WORD(mt_lg_next_render_advance);      /* seq_advance in the render loop (veneer 0x600bc2d9) */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define LG_MAGIC            0x474C544Du     /* "MTLG" */
#define LG_NONE             0xFFu

struct lg_state {
    uint32_t magic;
    void *seq;                              /* the copy's sequencer */
    uint8_t track;                          /* the copy's track, LG_NONE: no copy */
    uint8_t rec[REC_SIZE];                  /* the old note's record */
};
static struct lg_state lg_state_ram __attribute__((section(".mt_ram")));
#define LG (&lg_state_ram)

static void lg_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (LG->magic == LG_MAGIC)
        return;
    LG->track = LG_NONE;
    LG->seq = 0;
    LG->magic = LG_MAGIC;
}

/* End the kept note, if any. */
static void lg_flush(void)
{
    uint32_t t = LG->track;
    if (t == LG_NONE)
        return;
    LG->track = LG_NONE;
    ((note_off_fn)mt_lg_next_note_off)(LG->seq, t, LG->rec, NOTE_OFF_MODE);
}

static int is_midi(uint32_t instr)
{
    return instr >= INSTR_MIDI_FIRST && instr <= INSTR_MIDI_LAST;
}

static int has_fx(const uint8_t *rec, uint32_t type)
{
    return rec[REC_FX1] == type || rec[REC_FX2] == type;
}

/* Should the new note on track t play legato after `last`? */
static int lg_wanted(const uint8_t *seq, uint32_t t, const uint8_t *last)
{
    if (t >= TRACKS)
        return 0;
    const uint8_t *trk = seq + SEQ_TRACK(t);
    const uint8_t *work = trk + TRK_WORK;
    if (!is_midi(work[REC_INSTR]) || !is_midi(last[REC_INSTR]) || !has_fx(work, FX_GLIDE))
        return 0;
    if (has_fx(work, FX_CHORD) || has_fx(work, FX_ARP) || has_fx(last, FX_CHORD) || has_fx(last, FX_ARP))
        return 0;
    return trk[TRK_GATE_OPEN] != 0;
}

/* ---- the two sites in seq_process_track (audio interrupt or render loop) --------- */

/* ITCM 0x24da: "bl seq_note_off_q(seq, t, last record, -4)", the old note before a
 * new one -> stub -> here */
void mt_lg_note_off(void *seq, uint32_t track, const uint8_t *last, int32_t mode)
{
    lg_init_if_needed();
    lg_flush();
    if (lg_wanted((const uint8_t *)seq, track, last)) {
        for (uint32_t i = 0; i < REC_SIZE; i++)
            LG->rec[i] = last[i];
        LG->seq = seq;
        LG->track = (uint8_t)track;
        return;
    }
    ((note_off_fn)mt_lg_next_note_off)(seq, track, last, mode);
}

/* ITCM 0x2fd8: "bl seq_trigger_note(seq, t, work record)", the step's note -> stub ->
 * here */
void mt_lg_trigger(void *seq, uint32_t track, const uint8_t *rec)
{
    lg_init_if_needed();
    if (LG->track != LG_NONE && (LG->track != track || LG->seq != seq))
        lg_flush();
    if (LG->track != LG_NONE && rec[REC_NOTE] == LG->rec[REC_NOTE] && rec[REC_INSTR] == LG->rec[REC_INSTR])
        lg_flush();                         /* the same note again: stock order */
    ((trigger_fn)mt_lg_next_trigger)(seq, track, rec);
    lg_flush();                             /* legato: the old note ends after the new one starts */
}

/* ---- the guard: once per audio block and render block ---------------------------- */

/* replaces "bl seq_advance" (veneer 0x600aa858) at 0x600aa7f8 in app_audio_block (chained) */
void mt_lg_seq_advance(void *seq, float seconds)
{
    ((advance_fn)mt_lg_next_seq_advance)(seq, seconds);
    lg_init_if_needed();
    lg_flush();
}

/* replaces "bl seq_advance" (veneer 0x600bc2d8) at 0x600bc27a in the render loop (chained) */
void mt_lg_render_advance(void *seq, float seconds)
{
    ((advance_fn)mt_lg_next_render_advance)(seq, seconds);
    lg_init_if_needed();
    lg_flush();
}

/* ---- version text: Config shows 1.2.0-<LEGATO_VERSION> ---------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" LEGATO_VERSION;
