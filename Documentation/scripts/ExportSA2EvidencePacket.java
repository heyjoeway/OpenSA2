// Address-driven Ghidra exporter for the user's private owned SA2 PC copy.
// The caller must direct every generated artifact into ignored work/. This
// script is distributable; its decompiled/disassembled output is not.
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Map;
import java.util.TreeMap;

public class ExportSA2EvidencePacket extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3) {
            throw new IllegalArgumentException(
                "Usage: ExportSA2EvidencePacket.java OUTPUT EXPECTED_MD5 ADDRESS=LABEL [...]");
        }
        Path output = Paths.get(args[0]).toAbsolutePath().normalize();
        Files.createDirectories(output);

        String expectedMd5 = args[1].toLowerCase();
        String programMd5 = currentProgram.getExecutableMD5();
        if (programMd5 == null || !programMd5.toLowerCase().equals(expectedMd5)) {
            throw new IllegalStateException("Ghidra program MD5 mismatch: expected " +
                expectedMd5 + ", got " + programMd5);
        }

        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) {
            throw new IllegalStateException("Ghidra decompiler could not open the SA2 PE program.");
        }
        try {
            println("SA2_EVIDENCE_EXPORT program=" + currentProgram.getName() +
                " output=" + output);
            for (int i = 2; i < args.length; i++) exportTarget(decompiler, output, args[i]);
        } finally {
            decompiler.dispose();
        }
    }

    private void exportTarget(DecompInterface decompiler, Path output, String specification)
            throws Exception {
        String[] pieces = specification.split("=", 2);
        if (pieces.length != 2 || pieces[0].isEmpty() || pieces[1].isEmpty()) {
            throw new IllegalArgumentException("Expected ADDRESS=NAME, received: " + specification);
        }
        String addressText = pieces[0].replaceFirst("(?i)^0x", "").toUpperCase();
        String label = pieces[1];
        if (!label.matches("[A-Za-z_][A-Za-z0-9_]*")) {
            throw new IllegalArgumentException("Unsafe function label: " + label);
        }

        Address entry = toAddr(addressText);
        Function function = getFunctionAt(entry);
        if (function == null || !function.getEntryPoint().equals(entry)) {
            throw new IllegalStateException(
                "No existing exact function at 0x" + addressText +
                "; this exporter is intentionally read-only");
        }

        DecompileResults result = decompiler.decompileFunction(function, 180, monitor);
        if (!result.decompileCompleted() || result.getDecompiledFunction() == null) {
            throw new IllegalStateException("Decompile failed at 0x" + addressText +
                ": " + result.getErrorMessage());
        }

        String stem = String.format("%08X-%s", entry.getOffset(), label);
        Path cPath = output.resolve(stem + ".c");
        Files.writeString(cPath, result.getDecompiledFunction().getC().strip() + "\n",
            StandardCharsets.UTF_8);
        Path assemblyPath = output.resolve(stem + ".asm.txt");
        Files.writeString(assemblyPath, disassembly(function), StandardCharsets.UTF_8);

        Map<String, String> callers = callers(function);
        Map<String, String> callees = callees(function);
        Path metadataPath = output.resolve(stem + ".json");
        Files.writeString(metadataPath, metadata(function, label, callers, callees),
            StandardCharsets.UTF_8);
        println("SA2_EVIDENCE_WRITTEN entry=0x" + addressText + " label=" + label +
            " c=" + cPath + " asm=" + assemblyPath + " metadata=" + metadataPath);
    }

    private String disassembly(Function function) throws Exception {
        StringBuilder text = new StringBuilder();
        InstructionIterator instructions = currentProgram.getListing().getInstructions(
            function.getBody(), true);
        while (instructions.hasNext() && !monitor.isCancelled()) {
            Instruction instruction = instructions.next();
            text.append(instruction.getAddress()).append("  ");
            for (byte value : instruction.getBytes()) {
                text.append(String.format("%02X", value & 0xff));
            }
            text.append("  ").append(instruction).append('\n');
        }
        if (monitor.isCancelled()) throw new IllegalStateException(
            "Disassembly cancelled for " + function.getEntryPoint());
        return text.toString();
    }

    private Map<String, String> callers(Function function) {
        Map<String, String> result = new TreeMap<>();
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(
            function.getEntryPoint());
        while (references.hasNext()) {
            Reference reference = references.next();
            if (!reference.getReferenceType().isCall()) continue;
            Function caller = getFunctionContaining(reference.getFromAddress());
            String address = reference.getFromAddress().toString();
            result.put(address, caller == null ? "<no-function>" : caller.getName());
        }
        return result;
    }

    private Map<String, String> callees(Function function) throws Exception {
        Map<String, String> result = new TreeMap<>();
        for (Function callee : function.getCalledFunctions(monitor)) {
            result.put(callee.getEntryPoint().toString(), callee.getName());
        }
        return result;
    }

    private String metadata(Function function, String requestedLabel, Map<String, String> callers,
            Map<String, String> callees) {
        StringBuilder json = new StringBuilder();
        json.append("{\n");
        json.append("  \"program\": \"").append(escape(currentProgram.getName())).append("\",\n");
        json.append("  \"executable_md5\": \"").append(
            escape(currentProgram.getExecutableMD5())).append("\",\n");
        json.append("  \"image_base\": \"0x").append(currentProgram.getImageBase()).append("\",\n");
        json.append("  \"language_id\": \"").append(
            escape(currentProgram.getLanguageID().toString())).append("\",\n");
        json.append("  \"compiler_spec_id\": \"").append(
            escape(currentProgram.getCompilerSpec().getCompilerSpecID().toString())).append("\",\n");
        json.append("  \"entry\": \"0x").append(function.getEntryPoint()).append("\",\n");
        json.append("  \"requested_label\": \"").append(escape(requestedLabel)).append("\",\n");
        json.append("  \"ghidra_name\": \"").append(escape(function.getName())).append("\",\n");
        json.append("  \"address_count\": ").append(function.getBody().getNumAddresses()).append(",\n");
        appendMap(json, "callers", callers, true);
        appendMap(json, "callees", callees, false);
        json.append("}\n");
        return json.toString();
    }

    private void appendMap(StringBuilder json, String key, Map<String, String> values,
            boolean comma) {
        json.append("  \"").append(key).append("\": [\n");
        int index = 0;
        for (Map.Entry<String, String> value : values.entrySet()) {
            json.append("    {\"address\": \"0x").append(value.getKey())
                .append("\", \"function\": \"").append(escape(value.getValue())).append("\"}");
            if (++index < values.size()) json.append(',');
            json.append('\n');
        }
        json.append("  ]").append(comma ? ",\n" : "\n");
    }

    private String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
