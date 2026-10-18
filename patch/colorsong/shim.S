/* Entry points patched into song_grid_draw (0x60108eac) in place of
 * "bl eve_color_rgb" for filled blocks. At those call sites the callee-saved
 * registers hold: r9 (sb) = song control, r10 (sl) = visible row, r5 = track.
 * r0 already holds the stock theme color.
 */
    .syntax unified
    .thumb
    .text

    .global mt_color_hook_normal
    .type mt_color_hook_normal, %function
    .thumb_func
mt_color_hook_normal:           /* replaces bl at 0x60108fd2 */
    mov     r1, r9
    mov     r2, r10
    mov     r3, r5
    b.w     mt_block_color

    .global mt_color_hook_flagged
    .type mt_color_hook_flagged, %function
    .thumb_func
mt_color_hook_flagged:          /* replaces bl at 0x60108f7c */
    mov     r1, r9
    mov     r2, r10
    orr     r3, r5, #0x100
    b.w     mt_block_color
