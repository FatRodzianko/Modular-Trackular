// Write a plain-text index of the analysed program to the given output directory:
//   functions.tsv  entry, size, name
//   strings.tsv    address, referencing functions, text
// Usage (headless): -postScript ExportIndex.java <outdir>
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.Reference;
import ghidra.program.util.DefinedStringIterator;

import java.io.File;
import java.io.PrintWriter;
import java.util.TreeSet;

public class ExportIndex extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File out = new File(args.length > 0 ? args[0] : ".");
        out.mkdirs();

        int nf = 0;
        try (PrintWriter w = new PrintWriter(new File(out, "functions.tsv"), "UTF-8")) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                w.printf("%s\t%d\t%s%n", f.getEntryPoint(), f.getBody().getNumAddresses(), f.getName());
                nf++;
            }
        }

        // Make sure every printable run of 5+ chars in flash is a defined string first.
        int ns = 0;
        try (PrintWriter w = new PrintWriter(new File(out, "strings.tsv"), "UTF-8")) {
            for (Data s : DefinedStringIterator.forProgram(currentProgram)) {
                TreeSet<String> funcs = new TreeSet<>();
                for (Reference r : getReferencesTo(s.getAddress())) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    funcs.add(f != null ? f.getName() + "@" + f.getEntryPoint()
                                        : "nofunc@" + r.getFromAddress());
                }
                String text = String.valueOf(s.getValue()).replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r");
                w.printf("%s\t%s\t%s%n", s.getAddress(), String.join(",", funcs), text);
                ns++;
            }
        }
        println("exported " + nf + " functions, " + ns + " strings to " + out.getAbsolutePath());
    }
}
