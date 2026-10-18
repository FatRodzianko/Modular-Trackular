// Summarize references into a large range: one line per referenced address with the
// referring functions. Unlike RefsInRange.java it walks only the references Ghidra has,
// so it suits large ranges such as SDRAM.
// Usage (headless): -postScript RefsSummary.java <outfile> <start> <end>
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressIterator;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

import java.io.File;
import java.io.PrintWriter;

public class RefsSummary extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        AddressSet set = new AddressSet(toAddr(Long.decode(a[1])), toAddr(Long.decode(a[2]) - 1));
        try (PrintWriter w = new PrintWriter(new File(a[0]))) {
            AddressIterator it = currentProgram.getReferenceManager().getReferenceDestinationIterator(set, true);
            while (it.hasNext()) {
                Address addr = it.next();
                StringBuilder sb = new StringBuilder();
                int n = 0;
                for (Reference r : getReferencesTo(addr)) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    sb.append(" ").append(f != null ? f.getName() : r.getFromAddress().toString());
                    n++;
                }
                w.printf("%s %d%s%n", addr, n, sb);
            }
        }
    }
}
