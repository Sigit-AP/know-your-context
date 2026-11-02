// Inheritance with virtual and override.
public abstract class Animal : Object {
    public abstract string speak();

    public virtual string describe() {
        return speak();
    }
}

public class Dog : Animal {
    public override string speak() {
        return "woof";
    }
}
