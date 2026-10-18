/* Link address only: tools/patch_ptf.py places the code after the stock image or the
 * patches listed before it, and moves it there with build/humanize.rel. */
MEMORY
{
    PATCH (rx) : ORIGIN = 0x601BC000, LENGTH = 4K
}

SECTIONS
{
    .text : {
        KEEP(*(.text.mt_*))
        *(.text*)
        *(.rodata*)
    } > PATCH

    /* No initialised or zeroed RAM: humanize keeps everything on the stack. */
    .data : { *(.data*) } > PATCH
    .bss  : { *(.bss*) *(COMMON) } > PATCH
    ASSERT(SIZEOF(.data) == 0, "humanize must not use .data")
    ASSERT(SIZEOF(.bss) == 0, "humanize must not use .bss")

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
