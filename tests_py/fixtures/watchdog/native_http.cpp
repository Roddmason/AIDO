// Offline CLI protocol fixture: no network, environment access or credential reads.
// Native failure is the same __fastfail(7) used by native_fastfail.cpp.
#include <windows.h>
#include <intrin.h>
#pragma intrinsic(__fastfail)

static void output(const char* text) {
    DWORD count = 0, length = 0;
    while (text[length]) ++length;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), text, length, &count, 0);
}
static const wchar_t* find(const wchar_t* text, const wchar_t* needle) {
    for (; *text; ++text) {
        unsigned i = 0;
        while (needle[i] && text[i] == needle[i]) ++i;
        if (!needle[i]) return text;
    }
    return 0;
}
static bool exists(const wchar_t* name) { return GetFileAttributesW(name) != INVALID_FILE_ATTRIBUTES; }
static void signalReady() {
    HANDLE file = CreateFileW(L"ready.flag", GENERIC_WRITE, 0, 0, CREATE_NEW, 0, 0);
    if (file == INVALID_HANDLE_VALUE) ExitProcess(91);
    CloseHandle(file);
}
static void pressure() {
    HANDLE job = CreateJobObjectW(0, 0);
    static JOBOBJECT_EXTENDED_LIMIT_INFORMATION info;
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    info.JobMemoryLimit = 32 * 1024 * 1024;
    if (!job || !SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info, sizeof(info))
        || !AssignProcessToJobObject(job, GetCurrentProcess())) ExitProcess(92);
    // No host exhaustion: at most 96 MiB requested, 32 MiB native aggregate cap.
    for (int i = 0; i < 24; ++i) {
        volatile char* block = static_cast<volatile char*>(VirtualAlloc(0, 4 * 1024 * 1024, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        if (!block) {
            DWORD error = GetLastError();
            if (error != ERROR_COMMITMENT_LIMIT && error != ERROR_NOT_ENOUGH_MEMORY) ExitProcess(93);
            output("allocationRejectedUnder32MiBJob\n");
            return; // Keep the Job handle until fail-fast; never disable its limit.
        }
        for (int offset = 0; offset < 4 * 1024 * 1024; offset += 4096) block[offset] = 1;
    }
    ExitProcess(94); // A missing allocation failure is not PASS.
}
extern "C" void mainCRTStartup() {
    const wchar_t* argv = GetCommandLineW();
    if (find(argv, L"--version")) { output("codex-cli 0.149.0\n"); ExitProcess(0); }
    if (find(argv, L"--help")) {
        output("--ask-for-approval --sandbox --cd --config --disable --ephemeral --ignore-user-config --ignore-rules --strict-config --skip-git-repo-check --json\n"); ExitProcess(0);
    }
    if (find(argv, L"login status")) { output("Offline fixture, not a provider session\n"); ExitProcess(0); }
    const wchar_t* cd = find(argv, L"--cd ");
    if (!cd || !find(argv, L"exec")) ExitProcess(90);
    cd += 5;
    bool quoted = *cd == L'"';
    if (quoted) ++cd;
    static wchar_t workspace[4096];
    unsigned index = 0;
    while (*cd && *cd != (quoted ? L'"' : L' ') && index < 4095) workspace[index++] = *cd++;
    if (!SetCurrentDirectoryW(workspace)) ExitProcess(95);
    signalReady();
    for (int wait = 0; !exists(L"finish.flag"); ++wait) {
        if (wait == 1500) ExitProcess(96);
        Sleep(20);
    }
    if (exists(L"pressure.flag")) pressure();
    if (exists(L"fastfail.flag") || exists(L"pressure.flag")) __fastfail(7);
    output("{\"type\":\"thread.started\",\"thread_id\":\"offline-native-fixture\"}\n");
    output("{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"AIDO_READ_ONLY_SMOKE_OK\"}}\n");
    output("{\"type\":\"turn.completed\"}\n");
    ExitProcess(0);
}
