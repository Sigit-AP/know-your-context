// Static and instance methods.
package demo;

public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public static int clamp(int value, int low, int high) {
        if (value < low) return low;
        if (value > high) return high;
        return value;
    }

    private String describe() {
        return "calculator";
    }
}
