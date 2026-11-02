// Nested, inner and anonymous classes.
package demo;

class Outer {
    private int count = 0;

    static class Nested {
        int value() {
            return 1;
        }
    }

    class Inner {
        int bump() {
            return ++count;
        }
    }

    Runnable makeTask() {
        return new Runnable() {
            @Override
            public void run() {
                count++;
            }
        };
    }
}
