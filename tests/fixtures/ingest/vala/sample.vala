using GLib;

public class Greeter : Object {
    public string name;

    public void greet(string who) {
        helper(who);
    }

    private void helper(string x) {
        stdout.printf("%s", x);
    }
}

public interface Runner : Object {
    public abstract void run();
}
