// Signals and lambdas.
public class Emitter : Object {
    public signal void changed(int value);

    public void emit_change(int value) {
        changed(value);
    }
}

public class Listener : Object {
    public int seen = 0;

    public void connect_to(Emitter emitter) {
        emitter.changed.connect((value) => {
            seen = value;
        });
    }
}
