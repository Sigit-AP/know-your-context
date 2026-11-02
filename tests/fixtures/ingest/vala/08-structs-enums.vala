// Structs and enums with methods.
public struct Size {
    public int width;
    public int height;

    public int area() {
        return width * height;
    }
}

public enum Level {
    LOW,
    HIGH;

    public string to_string() {
        switch (this) {
            case LOW:
                return "low";
            case HIGH:
                return "high";
            default:
                assert_not_reached();
        }
    }
}
