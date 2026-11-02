// Static blocks, constants and factories.
package demo;

class Registry {
    static final int LIMIT = 100;
    private static int counter = 0;

    static {
        counter = LIMIT / 2;
    }

    static int next() {
        return ++counter;
    }

    static Registry create() {
        return new Registry();
    }

    int size() {
        return counter;
    }
}
