// Ghidra headless postScript: the analyzer's function map (design section 3.2).
//
// After auto-analysis it writes one JSON file, given as the script argument,
// with every function Ghidra found: entry address, body size, Ghidra's name and
// where that name came from, thunk flag, calling convention, resolved jump
// tables, direct call targets, import calls, indirect call sites, FS:[0] use,
// and the distinct data-reference targets. It also writes the memory map, the
// import table (IAT slot -> dll!name) and the program's entry points.
//
// It assigns no names and no regions: the region partition and the Mod Loader
// names are applied by Documentation/scripts/s1_function_map.py, which owns
// the rule and can be re-run without Ghidra. The output derives from the
// user's executable and belongs under private/generated (ignored).
//
// An optional second argument names a seed file, one "0xADDRESS source" per
// line (the driver writes it from the Mod Loader's function addresses and the
// direct-call targets of a linear sweep). Every seed inside an executable
// block that holds no instruction is disassembled and made a function, then
// the analyzer runs over the changes so the calls it finds are followed. The
// entry list before seeding is exported too, so the receipt can report what
// Ghidra found alone against what it found with the seeds.
//
// Usage (see s1_function_map.py for the exact command):
//   analyzeHeadless <project-dir> <project> -import sonic2app-unpacked.exe \
//     -scriptPath Documentation/scripts \
//     -postScript ExportSA2FunctionMap.java /abs/path/ghidra-function-map.json [seeds.txt]
import com.google.gson.stream.JsonWriter;
import ghidra.app.script.GhidraScript;
import ghidra.framework.Application;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressIterator;
import ghidra.program.model.listing.Bookmark;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.ExternalLocation;
import ghidra.program.model.symbol.ExternalReference;
import ghidra.program.model.symbol.FlowType;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;

import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

public class ExportSA2FunctionMap extends GhidraScript {
    private static final String MARKER = "SA2_FUNCTION_MAP";

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1 || args.length > 2) {
            throw new IllegalArgumentException(
                "Usage: ExportSA2FunctionMap.java OUTPUT_JSON [SEEDS_TXT]");
        }
        Path output = Paths.get(args[0]).toAbsolutePath().normalize();
        Files.createDirectories(output.getParent());
        long started = System.currentTimeMillis();

        Map<Address, String> imports = importSlots();
        println(MARKER + "_BEGIN program=" + currentProgram.getName() +
            " imports=" + imports.size());

        List<String> unseeded = new ArrayList<>();
        FunctionIterator before = currentProgram.getFunctionManager().getFunctions(true);
        while (before.hasNext()) unseeded.add(hex(before.next().getEntryPoint()));
        List<String[]> seedLog = new ArrayList<>();
        if (args.length == 2) seed(Paths.get(args[1]), seedLog);

        int functions = 0;
        try (BufferedWriter buffered = Files.newBufferedWriter(output, StandardCharsets.UTF_8);
             JsonWriter json = new JsonWriter(buffered)) {
            json.setIndent(" ");
            json.beginObject();
            json.name("schema").value("sa2-recomp-ghidra-function-map/v1");
            json.name("program").value(currentProgram.getName());
            json.name("executable_sha256").value(currentProgram.getExecutableSHA256());
            json.name("executable_md5").value(currentProgram.getExecutableMD5());
            json.name("image_base").value(hex(currentProgram.getImageBase()));
            json.name("language_id").value(currentProgram.getLanguageID().toString());
            json.name("compiler_spec_id").value(
                currentProgram.getCompilerSpec().getCompilerSpecID().toString());
            json.name("ghidra_version").value(Application.getApplicationVersion());

            json.name("unseeded_entries").beginArray();
            for (String address : unseeded) json.value(address);
            json.endArray();
            json.name("seeds").beginArray();
            for (String[] row : seedLog) {
                json.beginObject();
                json.name("address").value(row[0]);
                json.name("source").value(row[1]);
                json.name("result").value(row[2]);
                json.endObject();
            }
            json.endArray();

            json.name("entry_points").beginArray();
            AddressIterator entries = currentProgram.getSymbolTable().getExternalEntryPointIterator();
            while (entries.hasNext()) json.value(hex(entries.next()));
            json.endArray();

            json.name("memory_map").beginArray();
            for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
                json.beginObject();
                json.name("name").value(block.getName());
                json.name("start").value(hex(block.getStart()));
                json.name("end").value(hex(block.getEnd()));
                json.name("size").value(block.getSize());
                json.name("permissions").value(
                    (block.isRead() ? "r" : "-") + (block.isWrite() ? "w" : "-") +
                    (block.isExecute() ? "x" : "-"));
                json.name("initialized").value(block.isInitialized());
                json.endObject();
            }
            json.endArray();

            json.name("imports").beginArray();
            for (Map.Entry<Address, String> slot : imports.entrySet()) {
                if (slot.getKey().isExternalAddress()) continue;
                json.beginObject();
                json.name("iat").value(hex(slot.getKey()));
                json.name("symbol").value(slot.getValue());
                json.endObject();
            }
            json.endArray();

            json.name("functions").beginArray();
            FunctionIterator iterator = currentProgram.getFunctionManager().getFunctions(true);
            while (iterator.hasNext() && !monitor.isCancelled()) {
                writeFunction(json, iterator.next(), imports);
                functions++;
            }
            json.endArray();
            if (monitor.isCancelled()) throw new IllegalStateException("cancelled");

            // Function pointers stored in data (task tables, vtables, initializer
            // lists): every reference from a non-executable block to a function entry.
            json.name("data_function_pointers").beginArray();
            iterator = currentProgram.getFunctionManager().getFunctions(true);
            while (iterator.hasNext()) {
                Address entry = iterator.next().getEntryPoint();
                for (Reference reference : currentProgram.getReferenceManager().getReferencesTo(entry)) {
                    Address from = reference.getFromAddress();
                    if (!from.isMemoryAddress()) continue;
                    MemoryBlock block = currentProgram.getMemory().getBlock(from);
                    if (block == null || block.isExecute()) continue;
                    json.beginObject();
                    json.name("from").value(hex(from));
                    json.name("to").value(hex(entry));
                    json.endObject();
                }
            }
            json.endArray();

            json.name("function_count").value(functions);
            json.name("export_millis").value(System.currentTimeMillis() - started);
            json.endObject();
        }
        println(MARKER + "_WRITTEN path=" + output + " functions=" + functions);
    }

    // Seeds: "0xADDRESS source" lines. A seed that is already a function or
    // sits inside one is recorded and skipped; one outside executable memory is
    // refused; the rest are disassembled and made functions, then the analyzer
    // runs over the changes.
    private void seed(Path seeds, List<String[]> log) throws Exception {
        int created = 0;
        for (String line : Files.readAllLines(seeds, StandardCharsets.UTF_8)) {
            String[] parts = line.trim().split("\\s+");
            if (parts.length < 1 || parts[0].isEmpty()) continue;
            String source = parts.length > 1 ? parts[1] : "";
            Address address = toAddr(parts[0].replaceFirst("(?i)^0x", ""));
            String result;
            MemoryBlock block = currentProgram.getMemory().getBlock(address);
            if (block == null || !block.isExecute()) result = "not-executable";
            else if (getFunctionAt(address) != null) result = "already-function";
            else if (getFunctionContaining(address) != null) result = "inside-function";
            else if (getInstructionAt(address) == null && !disassemble(address)) result = "no-instruction";
            else if (createFunction(address, null) == null) result = "create-failed";
            else { result = "created"; created++; }
            log.add(new String[] {hex(address), source, result});
        }
        println(MARKER + "_SEEDED created=" + created + " of=" + log.size());
        if (created > 0) analyzeChanges(currentProgram);
    }

    // IAT slot address (and the external-space address) -> "DLL!name". The slot
    // is the data address whose external reference names the symbol; the PE
    // loader's ExternalLocation.getAddress() is not it (it held name-table RVAs).
    private Map<Address, String> importSlots() {
        Map<Address, String> slots = new TreeMap<>();
        Iterator<Symbol> externals = currentProgram.getSymbolTable().getExternalSymbols().iterator();
        while (externals.hasNext()) {
            Symbol symbol = externals.next();
            ExternalLocation location =
                currentProgram.getExternalManager().getExternalLocation(symbol);
            if (location == null) continue;
            String label = location.getLibraryName() + "!" + location.getLabel();
            slots.put(location.getExternalSpaceAddress(), label);
            for (Reference reference : currentProgram.getReferenceManager().getReferencesTo(symbol.getAddress())) {
                Address from = reference.getFromAddress();
                if (!from.isMemoryAddress()) continue;
                MemoryBlock block = currentProgram.getMemory().getBlock(from);
                if (block != null && !block.isExecute()) slots.put(from, label);
            }
        }
        return slots;
    }

    private void writeFunction(JsonWriter json, Function function, Map<Address, String> imports)
            throws Exception {
        Address entry = function.getEntryPoint();
        TreeSet<Address> directCalls = new TreeSet<>();
        TreeSet<String> importCalls = new TreeSet<>();
        TreeSet<Address> dataRefs = new TreeSet<>();
        List<Address> jumpSites = new ArrayList<>();
        List<TreeSet<Address>> jumpTargets = new ArrayList<>();
        int indirectCallSites = 0;
        int unresolvedJumps = 0;
        int fs0Sites = 0;
        int instructions = 0;

        InstructionIterator body = currentProgram.getListing().getInstructions(function.getBody(), true);
        while (body.hasNext()) {
            Instruction instruction = body.next();
            instructions++;
            if (instruction.toString().contains("FS:[0x0]")) fs0Sites++;
            FlowType flow = instruction.getFlowType();
            boolean sawImport = false;
            for (Reference reference : instruction.getReferencesFrom()) {
                RefType type = reference.getReferenceType();
                Address to = reference.getToAddress();
                String imported = imports.get(to);
                if (imported == null && reference.isExternalReference()) {
                    ExternalLocation location = ((ExternalReference) reference).getExternalLocation();
                    if (location != null) imported = location.getLibraryName() + "!" + location.getLabel();
                }
                if (imported != null) {
                    if (flow.isCall()) { importCalls.add(imported); sawImport = true; }
                    else dataRefs.add(to);
                    continue;
                }
                if (type.isCall() && !type.isComputed()) directCalls.add(to);
                else if (type.isData() && to.isMemoryAddress()) dataRefs.add(to);
            }
            if (flow.isCall() && flow.isComputed() && !sawImport) indirectCallSites++;
            if (flow.isJump() && flow.isComputed()) {
                TreeSet<Address> targets = new TreeSet<>();
                for (Address target : instruction.getFlows()) {
                    if (target.isMemoryAddress()) targets.add(target);
                }
                if (targets.isEmpty()) unresolvedJumps++;
                else { jumpSites.add(instruction.getAddress()); jumpTargets.add(targets); }
            }
        }

        json.beginObject();
        json.name("address").value(hex(entry));
        json.name("size").value(function.getBody().getNumAddresses());
        json.name("name").value(function.getName());
        json.name("name_source").value(function.getSymbol().getSource().name());
        json.name("fid").value(functionId(entry));
        json.name("thunk").value(function.isThunk());
        Function thunked = function.isThunk() ? function.getThunkedFunction(true) : null;
        if (thunked != null) {
            json.name("thunk_of").value(thunked.isExternal()
                ? thunked.getExternalLocation().getLibraryName() + "!" + thunked.getName()
                : hex(thunked.getEntryPoint()));
        }
        json.name("calling_convention").value(function.getCallingConventionName());
        json.name("signature_source").value(function.getSignatureSource().name());
        json.name("instructions").value(instructions);
        json.name("fs0_sites").value(fs0Sites);
        json.name("indirect_call_sites").value(indirectCallSites);
        json.name("unresolved_indirect_jumps").value(unresolvedJumps);
        json.name("jump_tables").beginArray();
        for (int i = 0; i < jumpSites.size(); i++) {
            json.beginObject();
            json.name("at").value(hex(jumpSites.get(i)));
            json.name("targets").beginArray();
            for (Address target : jumpTargets.get(i)) json.value(hex(target));
            json.endArray();
            json.endObject();
        }
        json.endArray();
        json.name("direct_calls").beginArray();
        for (Address target : directCalls) json.value(hex(target));
        json.endArray();
        json.name("import_calls").beginArray();
        for (String target : importCalls) json.value(target);
        json.endArray();
        json.name("data_refs").beginArray();
        for (Address target : dataRefs) json.value(hex(target));
        json.endArray();
        json.endObject();
    }

    // The Function ID analyzer records its match as a bookmark on the entry.
    private String functionId(Address entry) {
        for (Bookmark bookmark : currentProgram.getBookmarkManager().getBookmarks(entry)) {
            if (bookmark.getCategory() != null && bookmark.getCategory().contains("Function ID")) {
                return bookmark.getComment();
            }
        }
        return null;
    }

    private static String hex(Address address) {
        return String.format("0x%08X", address.getOffset());
    }
}
