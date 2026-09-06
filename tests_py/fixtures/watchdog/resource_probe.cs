// Offline bounded VirtualAlloc probe. Never allocates more than 96 MiB of committed pages.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
class ResourceProbe {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr VirtualAlloc(IntPtr address, UIntPtr size, uint type, uint protect);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool VirtualFree(IntPtr address, UIntPtr size, uint type);
    [DllImport("kernel32.dll")]
    static extern void RaiseFailFastException(IntPtr record, IntPtr context, uint flags);
    static void Main() {
        var blocks = new List<IntPtr>();
        long committed = 0;
        string line;
        while ((line = Console.ReadLine()) != null) {
            if (line == "exit") break;
            if (line == "exception") RaiseFailFastException(IntPtr.Zero, IntPtr.Zero, 0);
            if (line == "fastfail409") {
                // Explicit EXCEPTION_RECORD code, not a claim of the CRT's internal mechanism.
                IntPtr record = Marshal.AllocHGlobal(152);
                for (int i=0; i<152; i++) Marshal.WriteByte(record, i, 0);
                Marshal.WriteInt32(record, unchecked((int)0xC0000409));
                Marshal.WriteInt32(record, 4, 1);
                RaiseFailFastException(record, IntPtr.Zero, 0);
            }
            bool reserve = line == "reserve";
            int size = reserve ? 1024*1024*1024 : 4*1024*1024;
            if (!reserve && committed + size > 96*1024*1024) throw new InvalidOperationException("fixture bound");
            IntPtr ptr = VirtualAlloc(IntPtr.Zero, (UIntPtr)size, reserve ? 0x2000U : 0x3000U, 4);
            int error = ptr == IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
            if (ptr != IntPtr.Zero) {
                blocks.Add(ptr);
                if (!reserve) {
                    committed += size;
                    for (int offset=0; offset<size; offset+=4096) Marshal.WriteByte(ptr, offset, 1);
                }
            }
            var process = Process.GetCurrentProcess();
            Console.WriteLine("{\"success\":" + (ptr != IntPtr.Zero ? "true" : "false") +
                ",\"win32Error\":" + error + ",\"reservedOnly\":" + (reserve ? "true" : "false") +
                ",\"committedByFixture\":" + committed + ",\"rssBytes\":" + process.WorkingSet64 +
                ",\"privateBytes\":" + process.PrivateMemorySize64 + "}");
            Console.Out.Flush();
        }
        foreach (var ptr in blocks) VirtualFree(ptr, UIntPtr.Zero, 0x8000);
    }
}
