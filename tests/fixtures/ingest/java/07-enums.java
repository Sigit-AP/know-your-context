// Enums with fields and methods; records and annotations.
package demo;

enum Level {
    LOW(1),
    HIGH(2);

    private final int code;

    Level(int code) {
        this.code = code;
    }

    int code() {
        return code;
    }
}

record Point(int x, int y) {
}

@interface Tag {
    String value();
}
