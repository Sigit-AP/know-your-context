// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

struct Point {
    int256 x;
    int256 y;
}

enum Level {
    Low,
    High
}

struct Order {
    address maker;
    uint256 amount;
    Level level;
}

contract Registry {
    mapping(bytes32 => Order) public orders;

    function store(bytes32 id, Order calldata order) external {
        orders[id] = order;
    }
}
