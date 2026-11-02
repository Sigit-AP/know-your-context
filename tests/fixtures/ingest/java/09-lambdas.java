// Lambdas, method references and functional interfaces.
package demo;

import java.util.List;
import java.util.function.Function;
import java.util.stream.Collectors;

class Transform {
    List<Integer> doubled(List<Integer> values) {
        return values.stream().map(x -> x * 2).collect(java.util.stream.Collectors.toList());
    }

    List<String> upper(List<String> values) {
        return values.stream().map(String::toUpperCase).collect(java.util.stream.Collectors.toList());
    }

    Function<Integer, Integer> adder(int amount) {
        return x -> x + amount;
    }
}
