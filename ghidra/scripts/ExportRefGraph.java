// Export every reference Ghidra knows, by function, for whole-program analysis (for
// example: which functions and RAM only one feature's code uses).
// Usage (headless): -postScript ExportRefGraph.java <outfile>
// Output (tab-separated), one line per reference:
//   F <from function entry> <to function entry> <ref type>     code -> function (call or pointer)
//   R <from function entry> <to address> <ref type>            code -> anything else (RAM, data)
//   D <from data address> <to function entry> <ref type>       data (vtables, tables) -> function
//   X <function entry> <size> <name>                           every function
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.ReferenceManager;

import java.io.File;
import java.io.PrintWriter;

public class ExportRefGraph extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        ReferenceManager rm = currentProgram.getReferenceManager();
        try (PrintWriter w = new PrintWriter(new File(a[0]))) {
            FunctionIterator fi = currentProgram.getFunctionManager().getFunctions(true);
            while (fi.hasNext()) {
                Function f = fi.next();
                w.printf("X\t%s\t%d\t%s%n", f.getEntryPoint(), f.getBody().getNumAddresses(), f.getName());
                for (Address src : rm.getReferenceSourceIterator(f.getBody(), true)) {
                    for (Reference r : rm.getReferencesFrom(src)) {
                        Address to = r.getToAddress();
                        if (!to.isMemoryAddress())
                            continue;
                        Function g = getFunctionAt(to);
                        if (g != null)
                            w.printf("F\t%s\t%s\t%s%n", f.getEntryPoint(), g.getEntryPoint(), r.getReferenceType());
                        else
                            w.printf("R\t%s\t%s\t%s%n", f.getEntryPoint(), to, r.getReferenceType());
                    }
                }
            }
            fi = currentProgram.getFunctionManager().getFunctions(true);
            while (fi.hasNext()) {
                Function g = fi.next();
                ReferenceIterator it = rm.getReferencesTo(g.getEntryPoint());
                while (it.hasNext()) {
                    Reference r = it.next();
                    if (getFunctionContaining(r.getFromAddress()) == null)
                        w.printf("D\t%s\t%s\t%s%n", r.getFromAddress(), g.getEntryPoint(), r.getReferenceType());
                }
            }
        }
    }
}
