using System;
using System.Runtime.InteropServices;

// Windows shell metadata used by setup and its readback verification.
public static class SpectraShellProperties
{
    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyKey { public Guid Format; public uint Id; }
    [StructLayout(LayoutKind.Sequential)]
    private struct PropVariant
    {
        public ushort Type, Reserved1, Reserved2, Reserved3;
        public IntPtr Value, ReservedPointer;
    }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr context,
        uint flags, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);
    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref PropVariant value);

    public static string ReadAppId(string path)
    {
        return AppId(path, null);
    }

    public static string WriteAppId(string path, string appId)
    {
        if (String.IsNullOrEmpty(appId)) throw new ArgumentException("An application ID is required.", "appId");
        return AppId(path, appId);
    }

    private static string AppId(string path, string appId)
    {
        Guid iid = typeof(IPropertyStore).GUID;
        IPropertyStore store;
        SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, appId == null ? 0u : 2u, ref iid, out store);
        PropertyKey key = new PropertyKey { Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), Id = 5 };
        try
        {
            if (appId != null)
            {
                PropVariant value = new PropVariant { Type = 31, Value = Marshal.StringToCoTaskMemUni(appId) };
                try { store.SetValue(ref key, ref value); store.Commit(); }
                finally { PropVariantClear(ref value); }
            }
            PropVariant readback;
            store.GetValue(ref key, out readback);
            try { return readback.Type == 31 ? Marshal.PtrToStringUni(readback.Value) : null; }
            finally { PropVariantClear(ref readback); }
        }
        finally { Marshal.ReleaseComObject(store); }
    }
}
