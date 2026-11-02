// Import forms: plain, wildcard and static.
package demo;

import java.util.List;
import java.util.ArrayList;
import java.util.*;
import static java.util.Objects.requireNonNull;
import static java.lang.Math.max;

public class Imports {
    public int bigger(int a, int b) {
        List<Integer> xs = new ArrayList<>();
        requireNonNull(xs);
        return max(a, b);
    }
}
