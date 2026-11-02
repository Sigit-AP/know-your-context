// extends, super and overriding.
package demo;

class Animal {
    String speak() {
        return "...";
    }
}

class Dog extends Animal {
    @Override
    String speak() {
        return super.speak() + " woof";
    }
}

final class Puppy extends Dog {
}
