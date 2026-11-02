// Method invocations parking unresolved refs.
package demo;

class Runner {
    private int helper(int value) {
        return value * 2;
    }

    int run(int seed) {
        int total = helper(seed);
        return helper(total);
    }
}
