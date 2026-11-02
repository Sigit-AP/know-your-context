// using directives for namespaces.
using GLib;
using Gee;

public class Imports {
    public static string describe() {
        var list = new Gee.ArrayList<string>();
        list.add("a");
        return list.size.to_string();
    }
}
