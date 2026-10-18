/* versiontest: the version format string, moved past the stock image's last flash
 * sector. The device shows "1.2.0-MT0" only if the bootloader accepted the modified
 * literal pool and programmed this new sector.
 */
    .section .rodata
    .global mt_version_fmt
    .type mt_version_fmt, %object
mt_version_fmt:
    .asciz "%d.%d.%d-MT0"
    .size mt_version_fmt, . - mt_version_fmt
