// Generic classes and methods.
public class Box<T> : Object {
    private T item;

    public Box(T item) {
        this.item = item;
    }

    public T unwrap() {
        return item;
    }
}

public T identity<T>(T value) {
    return value;
}

public T? first<T>(Gee.List<T> items) {
    if (items.size == 0) {
        return null;
    }
    return items.get(0);
}
