// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Custom errors, require and revert.
error Unauthorized(address caller);
error InvalidAmount(uint256 amount);

contract Vault {
    address public immutable owner;

    constructor() {
        owner = msg.sender;
    }

    function withdraw(uint256 amount) external {
        if (msg.sender != owner) {
            revert Unauthorized(msg.sender);
        }
        require(amount > 0, "zero amount");
        if (amount > address(this).balance) {
            revert InvalidAmount(amount);
        }
        payable(msg.sender).transfer(amount);
    }

    receive() external payable {}
}
