// Custom annotations with retention and targets.
package demo;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Retention(RetentionPolicy.RUNTIME)
@Target({ElementType.TYPE, ElementType.METHOD})
@interface Marker {
    String name() default "";
}

@Marker(name = "service")
class Service {
    @Marker
    void handle() {
    }
}
