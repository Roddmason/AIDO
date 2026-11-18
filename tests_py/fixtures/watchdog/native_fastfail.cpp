// Synthetic, offline __fastfail(FAST_FAIL_FATAL_APP_EXIT). No CRT, credentials or network.
extern "C" {
__declspec(dllimport) void* __stdcall VirtualAlloc(void*, unsigned __int64, unsigned long, unsigned long);
__declspec(dllimport) unsigned long __stdcall GetLastError();
__declspec(dllimport) void* __stdcall GetStdHandle(unsigned long);
__declspec(dllimport) int __stdcall WriteFile(void*, const void*, unsigned long, unsigned long*, void*);
__declspec(dllimport) int __stdcall ReadFile(void*, void*, unsigned long, unsigned long*, void*);
__declspec(dllimport) void __stdcall ExitProcess(unsigned int);
__declspec(noreturn) void __fastfail(unsigned int);
}
#pragma intrinsic(__fastfail)

extern "C" void mainCRTStartup() {
    unsigned long written = 0;
#ifdef PRESSURE
    // Upper bound 96 MiB; the test sets a 32 MiB target Job and reserves normal qa_light.
    for (int i = 0; i < 24; ++i) {
        volatile char* block = static_cast<volatile char*>(VirtualAlloc(0, 4*1024*1024, 0x3000, 4));
        if (!block) {
            unsigned long error = GetLastError();
            static char result[] = "allocationFailedWin32=00000\n";
            for (int offset=0; offset<5; ++offset) { result[sizeof(result)-3-offset] = '0' + error % 10; error /= 10; }
            WriteFile(GetStdHandle(-11), result, sizeof(result)-1, &written, 0);
            break;
        }
        for (int offset=0; offset<4*1024*1024; offset+=4096) block[offset] = 1;
    }
#endif
    WriteFile(GetStdHandle(-11), "ready\n", 6, &written, 0);
    char command = 0;
    ReadFile(GetStdHandle(-10), &command, 1, &written, 0);
    if (command == 'x') ExitProcess(123); // Abrupt target exit while its debugger is active.
    __fastfail(7);
}
