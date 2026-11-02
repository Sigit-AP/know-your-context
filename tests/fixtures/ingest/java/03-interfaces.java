// Interfaces with default and static methods.
package demo;

interface Shape {
    double area();

    default String describe() {
        return "area=" + area();
    }
}

interface Named {
    String name();
}

class Circle implements Shape, Named {
    private final double radius = 1.0;

    public double area() {
        return Math.PI * radius * radius;
    }

    public String name() {
        return "circle";
    }
}
