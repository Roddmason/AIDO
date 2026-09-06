// Deliberate native fatal exception in a disposable process; no network or authentication.
using System;
using System.Runtime.InteropServices;
class NativeCrash {
    [DllImport("kernel32.dll")]
    static extern void RaiseFailFastException(IntPtr record, IntPtr context, uint flags);
    static void Main() { RaiseFailFastException(IntPtr.Zero, IntPtr.Zero, 0); }
}
