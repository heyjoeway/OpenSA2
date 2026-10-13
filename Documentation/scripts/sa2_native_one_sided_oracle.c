/* Controlled one-sided-contact oracle for a private copy of an owned SA2 PC
 * installation. Live CL_ColPolCheckTouchRe calls receive source radius 5 until
 * one record is accepted, then source radius 7 until one record is accepted.
 * For their per-face branch only, the installed local-origin Y is made negative
 * and the one-sided bit is set. Accepted records are captured at the installed
 * affine-inverse callsite, after native acceptance and radius-sign correction.
 * The caller's CollisionWork radius is restored after each call.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

typedef struct {
    int32_t Version;
    void *Init, *Patches;
    int32_t PatchCount;
    void *Jumps;
    int32_t JumpCount;
    void *Calls;
    int32_t CallCount;
    void *Pointers;
    int32_t PointerCount;
} ModInfo;

EXPORT const ModInfo SA2ModInfo = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0};

typedef struct {
    uint32_t case_index;
    uint32_t call_index;
    uint32_t source_radius;
    uint32_t effective_radius;
    uint32_t local_origin_y;
    uint32_t injected_one_sided_mask;
    uint32_t late_scratch_1c_bits;
    uint32_t matrix_argument;
} AcceptedEvent;

#define EVENT_CAPACITY 128
static volatile uint32_t oracle_active __attribute__((used));
static volatile uint32_t oracle_case __attribute__((used));
static volatile uint32_t oracle_completed __attribute__((used));
static volatile uint32_t oracle_call_index __attribute__((used));
static volatile uint32_t oracle_event_count __attribute__((used));
static volatile uint32_t oracle_event_before __attribute__((used));
static volatile uint32_t oracle_saved_radius __attribute__((used));
static volatile uint32_t oracle_injected_mask __attribute__((used));
static volatile uint32_t oracle_overflow __attribute__((used));
static volatile uint32_t onframe_tick __attribute__((used));
static volatile uint32_t patches_installed;
static volatile uint32_t output_written;
static AcceptedEvent oracle_events[EVENT_CAPACITY] __attribute__((used));

/* Replaces 0x48D1D7-0x48D1DC. Replays the installed store, then changes only
 * the active oracle call's stack-local flag/Y. Branching on the stored mask is
 * equivalent to the overwritten JZ fed by the installed AND EAX,0x00800000. */
__attribute__((naked, used))
static void OneSidedBranchHook(void) {
    __asm__ __volatile__(
        "movl %eax, 0x1c(%esp)\n\t"
        "cmpl $0, _oracle_active\n\t"
        "je 1f\n\t"
        "movl $0x00800000, 0x1c(%esp)\n\t"
        "movl $0x00800000, _oracle_injected_mask\n\t"
        "orl $0x80000000, 0x64(%esp)\n\t"
        "1:\n\t"
        "cmpl $0, 0x1c(%esp)\n\t"
        "je 2f\n\t"
        "pushl $0x0048d1dd\n\t"
        "ret\n\t"
        "2:\n\t"
        "pushl $0x0048d1fc\n\t"
        "ret\n\t");
}

/* Replaces only the CALL displacement at 0x48DA4C. This address is reached
 * only after a sample has been accepted. Integer-only capture leaves the live
 * x87 state untouched, then the wrapper invokes the installed inverse exactly
 * as the original callsite did. */
__attribute__((naked, used))
static void AcceptedRecordHook(void) {
    __asm__ __volatile__(
        "pushfl\n\t"
        "pushal\n\t"
        "cmpl $0, _oracle_active\n\t"
        "je 3f\n\t"
        "movl _oracle_event_count, %eax\n\t"
        "cmpl $128, %eax\n\t"
        "jb 1f\n\t"
        "movl $1, _oracle_overflow\n\t"
        "jmp 3f\n\t"
        "1:\n\t"
        "imull $32, %eax, %eax\n\t"
        "leal _oracle_events(%eax), %edx\n\t"
        "movl _oracle_case, %eax\n\t"
        "movl %eax, 0(%edx)\n\t"
        "movl _oracle_call_index, %eax\n\t"
        "movl %eax, 4(%edx)\n\t"
        "movl 8(%esp), %eax\n\t"
        "movl 8(%eax), %eax\n\t"
        "movl (%eax), %ecx\n\t"
        "movl %ecx, 8(%edx)\n\t"
        /* Relative to native pre-push ESP, CALL return + pushed argument +
         * pushfl/pushal place function-frame locals 0x2c bytes above this
         * hook frame. The operands below include their original local slots. */
        "movl 0x44(%esp), %ecx\n\t"
        "movl %ecx, 12(%edx)\n\t"
        "movl 0x90(%esp), %ecx\n\t"
        "movl %ecx, 16(%edx)\n\t"
        "movl _oracle_injected_mask, %ecx\n\t"
        "movl %ecx, 20(%edx)\n\t"
        /* [ESP+0x1C] was reused by native orientation math before this hook.
         * Preserve it explicitly as late scratch, never as a flag witness. */
        "movl 0x48(%esp), %ecx\n\t"
        "movl %ecx, 24(%edx)\n\t"
        "movl 0x28(%esp), %ecx\n\t"
        "movl %ecx, 28(%edx)\n\t"
        "incl _oracle_event_count\n\t"
        "3:\n\t"
        "popal\n\t"
        "popfl\n\t"
        "pushl 4(%esp)\n\t"
        "movl 8(%esp), %ecx\n\t"
        "movl $0x00427040, %eax\n\t"
        "call *%eax\n\t"
        "addl $4, %esp\n\t"
        "ret\n\t");
}

/* Replaces the CL_ColPolCheckTouchRe CALL at 0x46250A. After tick 60, repeat
 * each case until the native function constructs at least one accepted record.
 * The live work radius is changed for the complete call and restored before
 * returning to the game. */
__attribute__((naked, used))
static int __cdecl ContactCallHook(void) {
    __asm__ __volatile__(
        "incl _oracle_call_index\n\t"
        "cmpl $60, _onframe_tick\n\t"
        "jb 4f\n\t"
        "cmpl $2, _oracle_completed\n\t"
        "jae 4f\n\t"
        "movl 4(%esp), %edx\n\t"
        "movl (%edx), %ecx\n\t"
        "movl %ecx, _oracle_saved_radius\n\t"
        "movl _oracle_completed, %ecx\n\t"
        "movl %ecx, _oracle_case\n\t"
        "testl %ecx, %ecx\n\t"
        "jnz 1f\n\t"
        "movl $0x40a00000, (%edx)\n\t"
        "jmp 2f\n\t"
        "1:\n\t"
        "movl $0x40e00000, (%edx)\n\t"
        "2:\n\t"
        "movl _oracle_event_count, %ecx\n\t"
        "movl %ecx, _oracle_event_before\n\t"
        "movl $0, _oracle_injected_mask\n\t"
        "movl $1, _oracle_active\n\t"
        "pushl %ebx\n\t"
        "pushl 12(%esp)\n\t"
        "pushl 12(%esp)\n\t"
        "movl $0x0048ce40, %ebx\n\t"
        "call *%ebx\n\t"
        "addl $8, %esp\n\t"
        "popl %ebx\n\t"
        "pushl %eax\n\t"
        "movl 8(%esp), %edx\n\t"
        "movl _oracle_saved_radius, %ecx\n\t"
        "movl %ecx, (%edx)\n\t"
        "movl $0, _oracle_active\n\t"
        "movl _oracle_event_count, %ecx\n\t"
        "cmpl _oracle_event_before, %ecx\n\t"
        "jbe 3f\n\t"
        "incl _oracle_completed\n\t"
        "3:\n\t"
        "popl %eax\n\t"
        "ret\n\t"
        "4:\n\t"
        "pushl %ebx\n\t"
        "pushl 12(%esp)\n\t"
        "pushl 12(%esp)\n\t"
        "movl $0x0048ce40, %ebx\n\t"
        "call *%ebx\n\t"
        "addl $8, %esp\n\t"
        "popl %ebx\n\t"
        "ret\n\t");
}

static int call_matches(const unsigned char *site, const void *target) {
    return *site == 0xe8 &&
        site + 5 + *(const int32_t *)(site + 1) == target;
}

static int patch_call(unsigned char *site, const void *hook) {
    DWORD old_protection;
    DWORD ignored;
    int32_t displacement;
    if (!VirtualProtect(site + 1, 4, PAGE_EXECUTE_READWRITE, &old_protection))
        return 0;
    displacement = (const unsigned char *)hook - (site + 5);
    *(int32_t *)(site + 1) = displacement;
    VirtualProtect(site + 1, 4, old_protection, &ignored);
    FlushInstructionCache(GetCurrentProcess(), site, 5);
    return 1;
}

EXPORT void __cdecl Init(const char *path, const void *helpers,
                         unsigned int mod_index) {
    static const unsigned char branch_expected[] = {
        0x89, 0x44, 0x24, 0x1c, 0x74, 0x1f
    };
    unsigned char *branch = (unsigned char *)0x0048d1d7;
    unsigned char *contact_call = (unsigned char *)0x0046250a;
    unsigned char *inverse_call = (unsigned char *)0x0048da4c;
    DWORD old_protection;
    DWORD ignored;
    int32_t displacement;
    (void)path;
    (void)helpers;
    (void)mod_index;
    if (memcmp(branch, branch_expected, sizeof(branch_expected)) != 0 ||
        !call_matches(contact_call, (const void *)0x0048ce40) ||
        !call_matches(inverse_call, (const void *)0x00427040))
        return;
    if (!VirtualProtect(branch, sizeof(branch_expected), PAGE_EXECUTE_READWRITE,
                        &old_protection))
        return;
    displacement = (unsigned char *)&OneSidedBranchHook - (branch + 5);
    branch[0] = 0xe9;
    *(int32_t *)(branch + 1) = displacement;
    branch[5] = 0x90;
    VirtualProtect(branch, sizeof(branch_expected), old_protection, &ignored);
    FlushInstructionCache(GetCurrentProcess(), branch, sizeof(branch_expected));
    if (!patch_call(inverse_call, &AcceptedRecordHook))
        return;
    /* Install the outer activator last so any earlier partial setup remains
     * behaviorally inert if a later page-protection operation fails. */
    if (!patch_call(contact_call, &ContactCallHook))
        return;
    patches_installed = 3;
}

static void write_output(void) {
    FILE *file = fopen("mods/sa2-one-sided-oracle-v1.csv", "wb");
    uint32_t index;
    if (!file) return;
    fputs("event,case_index,call_index,source_radius_bits,"
          "effective_radius_bits,local_origin_y_bits,injected_one_sided_mask,"
          "late_scratch_1c_bits,matrix_argument,patches,overflow\n", file);
    for (index = 0; index < oracle_event_count && index < EVENT_CAPACITY; ++index) {
        const AcceptedEvent *event = &oracle_events[index];
        fprintf(file,
                "%lu,%lu,%lu,0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                "0x%08lx,0x%08lx,%lu,%lu\n",
                (unsigned long)index,
                (unsigned long)event->case_index,
                (unsigned long)event->call_index,
                (unsigned long)event->source_radius,
                (unsigned long)event->effective_radius,
                (unsigned long)event->local_origin_y,
                (unsigned long)event->injected_one_sided_mask,
                (unsigned long)event->late_scratch_1c_bits,
                (unsigned long)event->matrix_argument,
                (unsigned long)patches_installed,
                (unsigned long)oracle_overflow);
    }
    fclose(file);
    output_written = 1;
}

EXPORT void __cdecl OnFrame(void) {
    ++onframe_tick;
    if (!output_written && (oracle_completed >= 2 || onframe_tick >= 500))
        write_output();
    if (output_written && oracle_completed >= 2)
        ExitProcess(0);
}

EXPORT void __cdecl OnExit(void) {
    if (!output_written) write_output();
}
