// List every reference Ghidra knows to addresses in a range (e.g. a RAM struct), so
// unreferenced bytes stand out.
// Usage (headless): -postScript RefsInRange.java <outfile> <start> <end>
// Output lines: "<addr> <count> <from@function> ..." for referenced addresses, then
// the unreferenced runs of at least 4 bytes.
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

import java.io.File;
import java.io.PrintWriter;

public class RefsInRange extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        long start = Long.decode(a[1]), end = Long.decode(a[2]);
        try (PrintWriter w = new PrintWriter(new File(a[0]))) {
            long runStart = -1;
            for (long x = start; x < end; x++) {
                Address addr = toAddr(x);
                Reference[] refs = getReferencesTo(addr);
                if (refs.length == 0) {
                    if (runStart < 0) runStart = x;
                    continue;
                }
                if (runStart >= 0 && x - runStart >= 4)
                    w.printf("unreferenced %08x-%08x (%d bytes)%n", runStart, x, x - runStart);
                runStart = -1;
                StringBuilder sb = new StringBuilder();
                for (Reference r : refs) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    sb.append(" ").append(r.getFromAddress());
                    if (f != null) sb.append("@").append(f.getName());
                }
                w.printf("%08x %d%s%n", x, refs.length, sb);
            }
            if (runStart >= 0 && end - runStart >= 4)
                w.printf("unreferenced %08x-%08x (%d bytes)%n", runStart, end, end - runStart);
        }
    }
}
