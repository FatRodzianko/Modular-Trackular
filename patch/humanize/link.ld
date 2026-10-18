/* humanize starts at the first 4 KB flash sector after colorsong's code, so both fit
 * in one firmware (colorsong: 0x601bab80, under 5 KB). */
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
